import itertools
import math
from fractions import Fraction

import numpy as np
from scipy import stats

METHOD_EXACT='按班次等额配额精确枚举'
METHOD_MC='按班次等额配额 Monte Carlo 加一校正'
ASSUMPTIONS=[
    '无工艺效应：同一纸样上两种工艺对色差没有系统差别；观测到的读数对（读数1、读数2）随纸样固定，改派另一种顺序只改变配对差的符号。',
    '无顺序效应：先测原工艺还是先测新工艺，不改变该纸样两次读数的大小；否则重排得到的顺序差会混入顺序效应，需要另设顺序效应模型。',
    '缺测不受分派影响：纸样是否缺测与它被分派 AB 还是 BA 无关。缺测纸样保留原名册席位并计入班次配额，但不进入完整样本的工艺平均差。',
    '重排保留每班原配额：每班 AB/BA 的席位份数固定为等额，重排只重新决定哪些席位取 AB、哪些取 BA；逐张自由翻转会生成实际设计不可能的顺序，因此不采用。',
    '不同班次、不同纸样相互独立；每张纸样只出现一次。',
]


def finite_rows(snapshot,keys):
    if not snapshot: raise ValueError('空批次无法分析')
    result=[]
    for row in snapshot:
        values=row['values']
        if any(k not in values for k in keys): raise ValueError('观测字段不完整')
        x=[float(values[k]) for k in keys]
        if not all(math.isfinite(v) for v in x): raise ValueError('观测必须为有限数值')
        result.append(x)
    return np.asarray(result,dtype=float)


def sign_flip(differences,B=10000,seed=42):
    """Legacy per-paper enumeration for pre-roster snapshots (list inputs)."""
    d=np.asarray(differences,dtype=float)
    if len(d)<2 or not np.isfinite(d).all(): raise ValueError('至少2对完整有限观测')
    observed=float(np.mean(d));threshold=abs(observed)-1e-12
    if len(d)<=18:
        count=0;total=2**len(d)
        for start in range(0,total,2048):
            codes=np.arange(start,min(total,start+2048),dtype=np.uint64)
            signs=2*((codes[:,None]>>np.arange(len(d),dtype=np.uint64))&1).astype(float)-1
            count+=int(np.sum(np.abs(signs@d/len(d))>=threshold))
        return observed,count/total,'精确枚举',total
    if not 200<=B<=20000: raise ValueError('置换次数超出范围')
    rng=np.random.default_rng(seed);count=0
    for start in range(0,B,200):
        signs=rng.choice([-1.,1.],size=(min(200,B-start),len(d)))
        count+=int(np.sum(np.abs(signs@d/len(d))>=threshold))
    return observed,(count+1)/(B+1),'Monte Carlo加一校正',B


def _extract(snapshot):
    """Read a frozen roster snapshot; keep every sealed seat, complete or missing."""
    if not isinstance(snapshot,dict) or 'shifts' not in snapshot:
        raise ValueError('快照不是锁定名册格式，无法按班次分块检验')
    shifts=[];missing=[]
    for group in snapshot['shifts']:
        seats=int(group.get('seats',len(group.get('entries',[]))))
        items=[]
        for seat_index,entry in enumerate(group.get('entries',[])):
            done=entry.get('actual_order') is not None and entry.get('reading1') is not None and entry.get('reading2') is not None
            if done:
                r1=float(entry['reading1']);r2=float(entry['reading2'])
                if not (math.isfinite(r1) and math.isfinite(r2)): raise ValueError(f"纸样 {entry.get('slot_key')} 读数非有限数值")
                if entry['actual_order']!=entry.get('assigned_order'):
                    raise ValueError(f"纸样 {entry.get('slot_key')} 实际顺序与锁定分派不符")
                signed=1.0 if entry['actual_order']=='AB' else -1.0
                # Readings are stored in measured order; +1 = A then B, -1 = B then A.
                before=r1 if signed>0 else r2
                after=r2 if signed>0 else r1
                items.append({
                    'seat_index':seat_index,
                    'slot_key':entry.get('slot_key'),
                    'assigned_order':entry.get('assigned_order'),
                    'actual_order':entry.get('actual_order'),
                    'reading1':r1,'reading2':r2,'before':before,'after':after,
                    'difference':after-before,
                    # reading1/reading2 are stored in measured order; the pair
                    # difference is the order-flip unit used by the re-randomisation.
                    'pair_difference':r2-r1,
                    'signed':signed,
                })
            else:
                missing.append({'shift':group.get('shift'),'slot_key':entry.get('slot_key'),'assigned_order':entry.get('assigned_order')})
        shifts.append({
            'shift_index':group.get('shift_index'),'shift':group.get('shift'),
            'seats':seats,'quota_ab':int(group.get('quota_ab',seats//2)),'quota_ba':int(group.get('quota_ba',seats//2)),
            'complete':items,
        })
    return shifts,missing


def _blocks(shifts):
    """Per-shift complete-paper differences and the sealed seat quota."""
    blocks=[];total=0.0;n=0
    for group in shifts:
        items=group['complete']
        if not items: continue
        pair=np.asarray([x['pair_difference'] for x in items],dtype=float)
        signed=np.asarray([x['signed'] for x in items],dtype=float)
        blocks.append({
            'shift_index':group['shift_index'],'shift':group['shift'],'pair':pair,'signed':signed,'n':len(items),
            'seats':group['seats'],'quota_ab':group['quota_ab'],'quota_ba':group['quota_ba'],'missing':group['seats']-len(items),
        })
        total+=float(np.sum(signed*pair));n+=len(items)
    if n<2: raise ValueError('至少2对完整观测才能检验')
    return blocks,total/n,n


def _exact_block(block):
    """All complete-seat sign patterns with their seat-level multiplicities."""
    n=block['n'];quota=block['quota_ab'];missing=block['missing']
    low=max(0,quota-missing);high=min(quota,n)
    patterns=[]
    for k in range(low,high+1):
        multiplicity=math.comb(missing,quota-k)
        if multiplicity==0: continue
        for plus in itertools.combinations(range(n),k):
            signs=np.full(n,-1.0)
            if plus: signs[list(plus)]=1.0
            patterns.append((signs,multiplicity))
    return patterns


def _block_permutation_test(shifts,B,seed):
    blocks,observed,n=_blocks(shifts)
    threshold=abs(observed)-1e-12
    # Number of seat-level allocations the design allows, reported for auditability.
    combinations=1
    for b in blocks: combinations*=math.comb(b['seats'],b['quota_ab'])
    # Enumerating complete-seat sign patterns costs at most 2**n, so n<=18 stays cheap
    # even when many missing seats make the seat-level count astronomically large.
    if n<=18:
        count=0;total=0
        per_block=[_exact_block(b) for b in blocks]
        for combo in itertools.product(*per_block):
            stat=sum(float(np.dot(signs,b['pair'])) for (signs,_),b in zip(combo,blocks))/n
            weight=1
            for _,mult in combo: weight*=mult
            total+=weight
            if abs(stat)>=threshold: count+=weight
        # Seat-level weights can be huge (many missing seats); keep the ratio exact.
        return observed,float(Fraction(count,total)),METHOD_EXACT,total,blocks,combinations
    if not 200<=B<=20000: raise ValueError('置换次数超出范围')
    rng=np.random.default_rng(seed);count=0
    for start in range(0,B,200):
        batch=min(200,B-start)
        stats=np.zeros(batch)
        for b in blocks:
            seats=b['seats'];quota=b['quota_ab'];n_b=b['n']
            # Draw how many of the quota AB seats land on complete papers
            # (hypergeometric), then which complete papers they are. This is
            # O(complete) per replicate instead of O(all seats).
            for row in range(batch):
                k=int(rng.hypergeometric(n_b,seats-n_b,quota))
                signs=np.full(n_b,-1.0)
                if k: signs[rng.permutation(n_b)[:k]]=1.0
                stats[row]+=float(np.dot(signs,b['pair']))
        stats/=n
        count+=int(np.sum(np.abs(stats)>=threshold))
    return observed,(count+1)/(B+1),METHOD_MC,B,blocks,combinations


def calculate_roster(snapshot,params):
    B=int(params.get('resamples',10000))
    shifts,missing=_extract(snapshot)
    seed=int(params.get('seed',42))
    observed,p,method,permutations,blocks,combinations=_block_permutation_test(shifts,B,seed)
    pairs=[x for group in shifts for x in group['complete']]
    d=np.asarray([x['difference'] for x in pairs],dtype=float)
    before=np.asarray([x['before'] for x in pairs],dtype=float)
    after=np.asarray([x['after'] for x in pairs],dtype=float)
    mean=float(np.mean(d));n=len(d)
    sd=float(np.std(d,ddof=1));se=sd/math.sqrt(n);q=float(stats.t.ppf(.975,n-1));ci=[mean-q*se,mean+q*se]
    seats=sum(group['seats'] for group in shifts)
    quotas=[]
    for b in blocks:
        group=next(g for g in shifts if g['shift_index']==b['shift_index'])
        observed_plus=int(np.sum(b['signed']>0))
        quotas.append({
            '班次':b['shift'],
            '名册席位':b['seats'],
            '完整纸样':b['n'],
            '缺测席位':b['missing'],
            'AB席位配额':b['quota_ab'],
            'BA席位配额':b['quota_ba'],
            '完整样本中AB/BA':f'{observed_plus}/{b["n"]-observed_plus}',
            '班次平均差':float(np.mean([x['difference'] for x in group['complete']])),
            '席位层面可能分派数':math.comb(b['seats'],b['quota_ab']),
        })
    basis={
        '设计':'按班次分块；每班等额随机分派 AB/BA 顺序，锁定后不重抽',
        '统计量':'完整纸样的工艺平均差（新−原）；配对差按实际测量顺序记录',
        '重排规则':f'每班固定 AB 席位配额={"/".join(str(b["quota_ab"]) for b in blocks)}，重排只改变每班内哪些席位取 AB、哪些取 BA；缺测纸样保留原席位并计入配额',
        '随机种子':seed,
        '名册席位数':seats,
        '完整配对样本量':n,
        '缺测席位数':len(missing),
        '检验方法':method,
        '重排次数':permutations,
        '席位层面可能分派总数':combinations,
        '班次配额与依据':quotas,
        '假设':ASSUMPTIONS,
        '冻结名册':snapshot.get('roster',{}),
    }
    missing_note=f'名册{seats}席，完整{n}对，缺测{len(missing)}席仍占原名册席位；' if missing else f'名册{seats}席，完整{n}对；'
    return {
        'metrics':{
            '名册席位数':seats,
            '完整配对样本量':n,
            '缺测席位数':len(missing),
            '原工艺平均ΔE':float(before.mean()),
            '新工艺平均ΔE':float(after.mean()),
            '平均差(新减原)':mean,
            '差值标准差':sd,
            '双侧符号翻转p':p,
            '均值差95%t区间':ci,
            '计算方法':method,
            '枚举或模拟次数':permutations,
        },
        'interpretation':f"{missing_note}新工艺比原工艺平均色差{'增加' if mean>=0 else '减少'}{abs(mean):.4f} ΔE；双侧按班次等额配额符号翻转 p={p:.6g}（{method}）。",
        'caveat':'每对来自同一纸张，不同纸张相互独立。按班次等额重排依赖“无工艺效应”与“缺测不受分派影响”两条假设；缺测纸样保留名册席位但不进入完整样本平均差。t区间另依赖均值正态近似。未拒绝不意味着等效。',
        'columns':['班次','纸样编号','分派顺序','实际顺序','读数1','读数2','原工艺ΔE','新工艺ΔE','差(新−原)','完整性'],
        'rows':[[group['shift'],x['slot_key'],x['assigned_order'],x['actual_order'],x['reading1'],x['reading2'],x['before'],x['after'],x['difference'],'完整'] for group in shifts for x in group['complete']]
            +[[item['shift'],item['slot_key'],item['assigned_order'],'缺测','—','—','—','—','—','缺测（保留席位）'] for item in missing],
        'table_title':'每张纸样的实际顺序、读数与完整性',
        'test_basis':basis,
    }


def calculate(snapshot,params):
    if isinstance(snapshot,dict) and 'shifts' in snapshot:
        return calculate_roster(snapshot,params)
    x=finite_rows(snapshot,['before','after']);d=x[:,1]-x[:,0]
    mean,p,method,B=sign_flip(d,int(params.get('resamples',10000)))
    sd=float(np.std(d,ddof=1));se=sd/math.sqrt(len(d));q=float(stats.t.ppf(.975,len(d)-1));ci=[mean-q*se,mean+q*se]
    return {'metrics':{'配对样本量':len(d),'原工艺平均ΔE':float(x[:,0].mean()),'新工艺平均ΔE':float(x[:,1].mean()),'平均差(新减原)':mean,'差值标准差':sd,'双侧符号翻转p':p,'均值差95%t区间':ci,'计算方法':method,'枚举或模拟次数':B},'interpretation':f"新工艺比原工艺平均色差{'增加' if mean>=0 else '减少'}{abs(mean):.4f} ΔE；双侧符号翻转 p={p:.6g}（{method}）。",'caveat':'每对来自同一纸张，不同纸张相互独立。符号翻转检验要求零假设下差值符号可交换（如对称性）；t区间另依赖均值正态近似。未拒绝不意味着等效。','columns':['纸样编号','原工艺ΔE','新工艺ΔE','差(新−原)'],'rows':[[r['source_key'],float(v[0]),float(v[1]),float(v[1]-v[0])] for r,v in zip(snapshot,x)],'table_title':'每张纸样的配对差与完整性'}

"""Observable decision conditions selected against dated, source-bound baselines."""
import math

SPECS={
    'cash_conversion':('Operating cash flow / net income','times','cashflow'),
    'revenue_yoy_pct':('Comparable-quarter revenue growth','%','revenue'),
    'op_income_margin_pct':('Operating margin','%','op_income'),
    'gross_profit_margin_pct':('Gross margin','%','gross_profit'),
    'free_cash_flow_yoy_pct':('Comparable-window free cash flow growth','%','cashflow'),
    'receivables_yoy_pct':('Receivables growth','%','receivables'),
}


def baselines(analysis):
    fund=analysis.get('fundamentals',{});evidence=analysis.get('fundamental_evidence',{});out={}
    for key,(label,unit,period) in SPECS.items():
        value=fund.get(key);ids=evidence.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or not ids:continue
        window=fund.get('cashflow_window',{}) if period=='cashflow' else {'end':fund.get(period+'_period')}
        if not window.get('end'):continue
        out[key]={'label':label,'value':value,'unit':unit,'window':window,'evidence_ids':ids,'kind':'reported_metric'}
    if analysis.get('technical_evidence') and analysis.get('technical_as_of'):
        for key,label in [('prior_20_high','Prior 20-session high'),('prior_20_low','Prior 20-session low'),('ma50','50-session average'),('ma200','200-session average')]:
            value=analysis.get('technicals',{}).get(key)
            if type(value) in (int,float) and math.isfinite(value) and value>0:
                out[key]={'label':label,'value':value,'unit':'price units','window':{'end':analysis['technical_as_of']},'evidence_ids':analysis['technical_evidence'],'kind':'market_level'}
    return out


def schema(analysis):
    def obj(props):return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
    choices=[];available=baselines(analysis)
    for kind,windows in [('reported_metric',['next comparable reporting window','next two comparable reporting windows']),
                         ('market_level',['next completed trading session','next two completed trading sessions'])]:
        ids=[key for key,value in available.items() if value['kind']==kind]
        if ids:
            choices.append(obj({'kind':{'type':'string','enum':[kind]},
                'baseline_id':{'type':'string','enum':ids},
                'comparison':{'type':'string','enum':['above','below']},
                'window':{'type':'string','enum':windows}}))
    choices.append(obj({'kind':{'type':'string','enum':['event']},
        'event':{'type':'string','minLength':20,'maxLength':350},
        'source_to_check':{'type':'string','enum':['next issuer earnings release','next issuer earnings call',
            'next SEC filing','issuer investor-relations update','regulatory decision publication',
            'clinical-trial results or registry update','exchange short-interest publication',
            'current issuer price and volume history','dated analyst consensus update']},
        'time_window':{'type':'string','minLength':3,'maxLength':80}}))
    return {'oneOf':choices}


def render(condition,analysis):
    if condition['kind'] in {'reported_metric','market_level'}:
        baseline=baselines(analysis).get(condition['baseline_id'])
        if not baseline or baseline['kind']!=condition['kind']:raise ValueError('Decision condition has no dated source-bound baseline')
        if condition['comparison'] not in {'above','below'}:raise ValueError('Invalid baseline comparison')
        windows=['next completed trading session','next two completed trading sessions'] if condition['kind']=='market_level' else ['next comparable reporting window','next two comparable reporting windows']
        if condition['window'] not in windows:raise ValueError('Invalid comparison window')
        suffix=' in each of the '+condition['window'] if condition['window'].startswith('next two') else ' in the '+condition['window']
        unit='%' if baseline['unit']=='%' else ' '+baseline['unit']
        if condition['kind']=='market_level':
            text=f'Price closes {condition["comparison"]} the {baseline["label"]} of {baseline["value"]:.2f}{unit} (as of {baseline["window"]["end"]}){suffix}.'
        else:
            text=f'{baseline["label"]} moves {condition["comparison"]} its reported baseline of {baseline["value"]:.2f}{unit} (window ending {baseline["window"]["end"]}){suffix}.'
        return text,baseline['evidence_ids'],True
    if condition['kind']!='event':raise ValueError('Invalid decision condition kind')
    return f'{condition["event"]} Check {condition["source_to_check"]}; timing: {condition["time_window"]}.',[],False

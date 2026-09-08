"""Recent saved investigations, scoped to the authorized workspace data root."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def generated_label(value):
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Report generation time must include a timezone')
    return stamp.astimezone(ZoneInfo('America/New_York')).strftime('%A, %d %b %Y · %I:%M %p %Z')


def recent_reports(data,limit=5):
    rows=[]
    for path in (Path(data)/'intelligence/investigations').glob('*/*/status.json'):
        try:
            status=json.loads(path.read_text())
            ticker,run_id=path.parent.parent.name,path.parent.name
            if status.get('state') not in {'complete','partial'} or not (path.parent/'report.json').is_file():
                continue
            if status.get('ticker')!=ticker or status.get('run_id')!=run_id:
                continue
            stamp=status['updated_at']
            label=generated_label(stamp)
            rows.append({'ticker':ticker,'run_id':run_id,'generated_at':stamp,
                         'label':label,'state':status['state']})
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            continue
    rows.sort(key=lambda row:(datetime.fromisoformat(row['generated_at'].replace('Z','+00:00')),row['run_id']),reverse=True)
    return rows[:limit]

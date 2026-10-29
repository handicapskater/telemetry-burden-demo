"""One-time, dependency-free conversion of the inspected source CSV."""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw/whoop_strava_join_candidates.csv'

def number(value):
    try:
        return float(value) if value else None
    except ValueError:
        return None

def opt(value):
    return value or None

def activity_name(value):
    value = value.strip()
    lower = value.lower()
    if 'skat' in lower:
        return 'skating'
    if 'walk' in lower:
        return 'walking'
    if 'commut' in lower or 'transport' in lower or 'e-bike' in lower:
        return 'transportation'
    return lower.replace(' ', '_') or 'unknown'

with RAW.open(newline='', encoding='utf-8-sig') as handle:
    reader = csv.DictReader(handle)
    columns = reader.fieldnames
    rows = list(reader)
whoop_keys = Counter((r['start_time_whoop'], r['activity_name_whoop'], r['duration_min']) for r in rows)
strava_keys = Counter(r['event_id_strava'] for r in rows if r['event_id_strava'])
records = []
for i, r in enumerate(rows, 1):
    wk = (r['start_time_whoop'], r['activity_name_whoop'], r['duration_min'])
    sid = r['event_id_strava']
    mapped = {
        'duration_minutes': 'duration_min', 'distance_miles': 'metrics.distance_miles',
        'heart_rate.mean_bpm': 'metrics.average_hr_bpm_whoop',
        'heart_rate.max_bpm': 'metrics.max_hr_bpm_whoop',
        'heart_rate.resting_bpm': 'metrics.resting_hr_bpm',
        'hrv.hrv_ms': 'metrics.hrv_ms', 'timestamp': 'start_time_whoop',
    }
    records.append({
        'record_id': f'row_{i:05d}', 'source_row': i, 'source': 'WHOOP',
        'sources': ['WHOOP'] + (['Strava'] if sid else []),
        'original_ids': {'whoop': opt(r['event_id_whoop']), 'strava': opt(sid)},
        'original_sources': {'whoop': r['entity_type_whoop'], 'strava': opt(r['entity_type_strava'])},
        'activity': activity_name(r['activity_name_whoop']),
        'original_activities': {'whoop': r['activity_name_whoop'], 'strava': opt(r['activity_name_strava']), 'strava_type': opt(r['activity_type'])},
        'timestamp': opt(r['start_time_whoop']),
        'original_timestamps': {'whoop_start': opt(r['start_time_whoop']), 'whoop_end': opt(r['end_time']), 'strava_start': opt(r['start_time_strava']), 'whoop_date': opt(r['event_date_whoop']), 'strava_date': opt(r['event_date_strava'])},
        'duration_minutes': number(r['duration_min']), 'distance_miles': number(r['metrics.distance_miles']),
        'heart_rate': {'mean_bpm': number(r['metrics.average_hr_bpm_whoop']), 'max_bpm': number(r['metrics.max_hr_bpm_whoop']), 'resting_bpm': number(r['metrics.resting_hr_bpm'])},
        'hrv': {'hrv_ms': number(r['metrics.hrv_ms']), 'rmssd_ms': None, 'sdnn_ms': None, 'context': 'WHOOP daily recovery; not an activity HRV measurement'},
        'join': {'likely_match': r['likely_match'].lower() == 'true', 'start_delta_min': number(r['start_delta_min']), 'whoop_candidate_count': whoop_keys[wk], 'strava_candidate_count': strava_keys[sid] if sid else 0, 'ambiguous': whoop_keys[wk] > 1 or bool(sid and strava_keys[sid] > 1)},
        'source_columns': mapped,
        'original_units': {'duration_minutes': 'minutes', 'distance_miles': 'miles', 'heart_rate': 'bpm', 'hrv': 'ms', 'start_delta_min': 'minutes'},
    })
coverage = Counter(x['activity'] for x in records)
quality = {
    'total_input_rows': len(rows), 'valid_records': len(records), 'rejected_records': 0,
    'missing_physiological_fields': {'mean_hr': sum(x['heart_rate']['mean_bpm'] is None for x in records), 'max_hr': sum(x['heart_rate']['max_bpm'] is None for x in records), 'hrv': sum(x['hrv']['hrv_ms'] is None for x in records), 'rmssd': len(records), 'sdnn': len(records)},
    'device_coverage': {'WHOOP': len(records), 'Strava': sum('Strava' in x['sources'] for x in records), 'Polar': 0},
    'activity_coverage': dict(coverage),
    'duplicate_records': sum(v - 1 for v in whoop_keys.values() if v > 1),
    'ambiguous_matches': sum(x['join']['ambiguous'] for x in records),
    'warnings': ['Join candidate rows are not unique workouts; deduplicate WHOOP event keys for statistics.', 'WHOOP HRV is a daily recovery metric, not activity-measured HRV.', 'No RMSSD or SDNN fields.', 'No Polar records.', 'No explicit walking activity; walking-versus-skating comparison unavailable.' if not coverage['walking'] else 'Walking activity present.'],
}
schema = {'input_columns': columns, 'column_mappings': mapped, 'record_model': 'app.schemas.TelemetryRecord', 'api_models': ['HealthResponse', 'TelemetryResponse', 'MetricsResponse', 'AnalysisResponse', 'IngestResponse', 'AskRequest', 'AskResponse'], 'workout_identity': ['start_time_whoop', 'activity_name_whoop', 'duration_min'], 'join_policy': 'Retain all candidates; mark repeated WHOOP and Strava keys ambiguous; deduplicate workout identity only during analysis.'}
for path, data in [(ROOT/'data/processed/telemetry.json', records), (ROOT/'data/metadata/schema.json', schema), (ROOT/'data/metadata/data_quality.json', quality)]:
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
print(json.dumps(quality, indent=2))

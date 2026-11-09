"""Finish the pre-freeze draft with the parent's final warmup-aware review."""
import hashlib
import json
from pathlib import Path

stage=Path(__file__).resolve().parent
partial=Path(r'C:\Users\admin\AppData\Local\Temp\r260401-brjd-20261004-partial-v9')


def sha(data):return hashlib.sha256(data).hexdigest()


def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


review_bytes=(partial/'PARTIAL_REVIEW_v10.json').read_bytes()
warmup_bytes=(partial/'WARMUP_RECOVERY_v10.json').read_bytes()
assert sha(review_bytes)=='9388ebe30779219334bc3f3271ae4c3e6370468f6e52c4fce6f9307ca070fb47'
assert sha(warmup_bytes)=='3769803a185b9418fe381084fe191af3157db6d80d39f8c53032feb458005ef8'
for relative,data in (('control_validation/R4_v10_PARTIAL_REVIEW.json',review_bytes),
                      ('control_validation/WARMUP_RECOVERY_v10.json',warmup_bytes)):
    with (stage/relative).open('xb') as handle:handle.write(data)
review=json.loads(review_bytes.decode('utf-8'))
proof=json.loads((stage/'RECOVERY_REVIEW_v10.json').read_text(encoding='utf-8'))
evidence=json.loads((stage/'control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json').read_text(encoding='utf-8'))
for value in (proof,evidence):
    value['partial_review_sha256']=sha(review_bytes)
    value['warmup_recovery_sha256']=sha(warmup_bytes)
    value['warmup_recovery_record']='control_validation/WARMUP_RECOVERY_v10.json'
    value['partial_validity_review']='control_validation/R4_v10_PARTIAL_REVIEW.json'
    value['stale_warmup_markers_quarantined']=True
    value['new_engine_untimed_warmup_required']=True
    value['old_warmup_marker_live_policy']='Only exact three quarantined old markers must be absent; all other20 live files must match frozen snapshot SHA'
    value['repeated_timing_measurements_authorized']=False
proof['recovery_evidence_sha256']=sha(encoded(evidence))
(stage/'control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json').write_bytes(encoded(evidence))
(stage/'RECOVERY_REVIEW_v10.json').write_bytes(encoded(proof))
protocol=json.loads((stage/'BRJD_EXECUTION_PROTOCOL_v10.json').read_text(encoding='utf-8'))
protocol['partial_recovery_review']['warmup_recovery_record']='control_validation/WARMUP_RECOVERY_v10.json'
protocol['partial_recovery_review']['partial_validity_review']='control_validation/R4_v10_PARTIAL_REVIEW.json'
protocol['partial_recovery_review']['old_warmup_markers_quarantined']=3
protocol['partial_recovery_review']['new_engine_untimed_warmup_required']=True
protocol['partial_recovery_review']['live_original_files_sha_verified']=20
protocol['partial_recovery_review']['timing_cells_remeasured']=False
(stage/'BRJD_EXECUTION_PROTOCOL_v10.json').write_bytes(encoded(protocol))
print(json.dumps({'status':'DRAFT_FINALIZED_BEFORE_FREEZE','proof_sha256':sha(encoded(proof))}))

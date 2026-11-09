"""Apply the reviewed R4-relative inventory layout before v10 is frozen."""
from pathlib import Path

stage = Path(__file__).resolve().parent
builder = (stage/'prepare_v10.py').read_text(encoding='utf-8')
namespace = {}
begin = builder.index("controller = (SOURCE/'controller_v9.py')")
end = builder.index("guard = (SOURCE/'scripts/common_guard_v9.py')")
namespace.update(SOURCE=Path(r'E:\r260401-brjd-20261003'))
exec(builder[begin:end], namespace)
(stage/'controller_v10.py').write_text(namespace['controller'],encoding='utf-8',newline='\n')
print('Updated controller before freeze; no deployed source modified')

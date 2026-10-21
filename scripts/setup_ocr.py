"""Download pinned Tesseract language data with checksum verification."""
import hashlib,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent.parent/'data/tools/tessdata'
root.mkdir(parents=True,exist_ok=True)
models={'rus':'e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225',
        'eng':'7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'}
for lang,digest in models.items():
    path=root/f'{lang}.traineddata'
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:continue
    url=f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{lang}.traineddata'
    data=urllib.request.urlopen(url,timeout=90).read()
    if hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError(f'{lang}: checksum mismatch; file not installed')
    path.write_bytes(data)
    print('Installed',lang)

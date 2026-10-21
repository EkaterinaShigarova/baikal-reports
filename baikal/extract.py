"""Resumable page-level PDF extraction. No model sees a whole PDF."""
from __future__ import annotations
import csv, html, io, json, re, subprocess, time, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pymupdf
from .core import DATA, EXTRACTOR_VERSION, atomic_json, atomic_text, page_dir, read_json

_DOC = None
_DEST = None


def _init(pdf, dest):
    global _DOC,_DEST
    _DOC=pymupdf.open(pdf);_DEST=Path(dest)


def ocr_page(page):
    tess=DATA/'tools/tessdata'
    if not all((tess/f'{lang}.traineddata').exists() for lang in ('rus','eng')):
        return '', {'status':'missing_language','message':'Install rus+eng Tesseract data via scripts/setup_ocr.py'}
    png=page.get_pixmap(dpi=180).tobytes('png')
    try:
        p=subprocess.run(['tesseract','stdin','stdout','--tessdata-dir',str(tess),'-l','rus+eng','tsv'],input=png,capture_output=True,timeout=120)
        if p.returncode: return '',{'status':'error','message':p.stderr.decode(errors='replace')[-400:]}
        rows=list(csv.DictReader(io.StringIO(p.stdout.decode('utf-8')),delimiter='\t'))
        lines={};scores=[]
        for r in rows:
            if r.get('text','').strip():
                key=(r['block_num'],r['par_num'],r['line_num'])
                lines.setdefault(key,[]).append(r['text'])
                if float(r['conf'])>=0:scores.append(float(r['conf']))
        return '\n'.join(' '.join(v) for v in lines.values()),{'status':'ok','mean_confidence':round(sum(scores)/len(scores),2) if scores else 0,'words':len(scores),'dpi':180}
    except (subprocess.TimeoutExpired,FileNotFoundError) as exc:return '',{'status':'error','message':str(exc)}


def numeric_candidates(text, page):
    out=[]
    pattern=r'(?<!\w)(?:[<>≤≥]\s*)?-?\d[\d\u00a0 ]*(?:[,.]\d+)?(?:\s*[-–]\s*\d+(?:[,.]\d+)?)?\s*(?:млн\s*|тыс\.?\s*)?(?:мг/дм[³3]|мг/л|мкг/л|км[²2³3]?|м[³3²2]|га|тонн|тыс\.\s*т|%|мг/кг)'
    for m in re.finditer(pattern,text):
        out.append({'raw':m[0].strip(),'quote':text[max(0,m.start()-150):m.end()+160].strip(),'page':page,'status':'candidate','territory':None,'year':None,'confidence':0.35})
    return out


def _extract_one(index):
    start=time.monotonic();page=_DOC[index];n=index+1;warnings=[]
    native=page.get_text(sort=True)
    alpha=sum(c.isalpha() for c in native)
    ocr={'status':'not_needed'};text=native;method='native'
    images=[];graphics=[]
    for info in page.get_image_info(xrefs=True):
        xref=info.get('xref',0);entry={'xref':xref,'bbox':list(info['bbox']),'width':info['width'],'height':info['height']}
        if xref:
            try:
                asset=_DOC.extract_image(xref);name=f'images/{xref}.{asset["ext"]}';path=_DEST/name
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():
                    # Same xref may occur on multiple pages; identical content is safe.
                    path.write_bytes(asset['image'])
                entry['asset']=name
            except Exception as exc:warnings.append(f'image {xref}: {exc}')
        images.append(entry)
    replacement=native.count('\ufffd')/max(len(native),1)
    if alpha<60 or replacement>0.03:
        ocrtext,ocr=ocr_page(page)
        if ocrtext.strip():text=ocrtext;method='ocr'
        if ocr['status']!='ok' or ocr.get('mean_confidence',0)<65:warnings.append('OCR requires review')
    blocks=page.get_text('dict',flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']
    spans=[s for b in blocks for l in b.get('lines',[]) for s in l['spans'] if s['text'].strip()]
    sizes=sorted(s['size'] for s in spans);base=sizes[len(sizes)//2] if sizes else 12
    headings=[{'text':s['text'],'bbox':list(s['bbox']),'size':s['size']} for s in spans if s['size']>base*1.17 and len(s['text'])>3]
    tables=[]
    try:
        found=page.find_tables()
        for table in found.tables:
            rows=table.extract()
            if len(rows)>1 and max(map(len,rows),default=0)>1:
                tables.append({'method':'pymupdf','bbox':list(table.bbox),'rows':rows,'status':'needs_review','confidence':0.55})
    except Exception as exc:warnings.append(f'table detection: {exc}')
    # Second engine only on explicitly captioned table pages without a detected table.
    if not tables and re.search(r'Таблица\s+\d',text,re.I) and method=='native':
        try:
            import pdfplumber
            with pdfplumber.open(_DOC.name,pages=[n]) as pdf:
                for t in pdf.pages[0].extract_tables():
                    if len(t)>1:tables.append({'method':'pdfplumber','rows':t,'status':'needs_review','confidence':0.4})
        except Exception as exc:warnings.append(f'table fallback: {exc}')
    captions=[line.strip() for line in text.splitlines() if re.match(r'\s*(?:Рис(?:унок|\.)?|Таблица|Карта|Схема)\s*[\d.]',line,re.I)]
    drawings=len(page.get_drawings())
    if images or captions or drawings>20:
        preview=f'previews/{n:04d}.png';path=_DEST/preview;path.parent.mkdir(parents=True,exist_ok=True)
        page.get_pixmap(dpi=80).save(path)
        graphics=[{'preview':preview,'captions':captions,'vector_paths':drawings,'status':'human_review','note':'No numerical values inferred from image pixels.'}]
    result={'page':n,'version':EXTRACTOR_VERSION,'status':'ok','text':text,'native_text':native,'method':method,'ocr':ocr,'headings':headings,
       'tables':tables,'images':images,'graphics':graphics,'captions':captions,'numbers':numeric_candidates(text,n),'warnings':warnings,'seconds':round(time.monotonic()-start,3)}
    if len(text.strip())<30:result['warnings'].append('little_or_no_text')
    atomic_json(_DEST/'pages'/f'{n:04d}.json',result)
    md=f'# PDF page {n}\n\n{text}\n'
    for i,t in enumerate(tables):md+=f'\n## Table candidate {i+1} ({t["method"]}; needs review)\n\n```json\n{json.dumps(t["rows"],ensure_ascii=False)}\n```\n'
    atomic_text(_DEST/'markdown'/f'{n:04d}.md',md)
    atomic_text(_DEST/'html'/f'{n:04d}.html','<!doctype html><meta charset="utf-8"><title>Page '+str(n)+'</title><pre>'+html.escape(text)+'</pre>')
    return {'page':n,'status':'ok','method':method,'warnings':warnings}


def process(source, workers=2, limit=None, retry_errors=False):
    dest=page_dir(source);dest.mkdir(parents=True,exist_ok=True)
    pdf=DATA/'raw'/f'{source["sha256"]}.pdf'
    with pymupdf.open(pdf) as doc:total=len(doc)
    count=min(total,limit) if limit else total
    pending=[];cached=0
    for n in range(1,count+1):
        p=read_json(dest/'pages'/f'{n:04d}.json')
        if p and p.get('version')==EXTRACTOR_VERSION and p.get('status')=='ok' and (not retry_errors or p.get('ocr',{}).get('status') not in ('error','missing_language')):cached+=1
        else:pending.append(n-1)
    if pending:
        with ProcessPoolExecutor(max_workers=workers,initializer=_init,initargs=(str(pdf),str(dest))) as pool:
            tasks={pool.submit(_extract_one,i):i for i in pending}
            for f in as_completed(tasks):
                n=tasks[f]+1
                try:f.result()
                except Exception as exc:atomic_json(dest/'pages'/f'{n:04d}.json',{'page':n,'version':EXTRACTOR_VERSION,'status':'error','error':str(exc)})
                if n%25==0:print(f'{source["id"]}: page {n}/{total}',flush=True)
    pages=[read_json(p) for p in sorted((dest/'pages').glob('*.json'))]
    good=[p for p in pages if p['status']=='ok']
    # Adjacent tables are linked as candidates; no unverified concatenation of data.
    continuation=[]
    for prev,cur in zip(good,good[1:]):
        if cur['page']!=prev['page']+1:continue
        if prev['tables'] and cur['tables']:
            a,b=prev['tables'][-1]['rows'],cur['tables'][0]['rows']
            if len(a[0])==len(b[0]) and (a[0]==b[0] or re.search(r'Продолжение\s+табл',cur['text'],re.I)):
                continuation.append({'pages':[prev['page'],cur['page']],'status':'candidate','reason':'same columns and repeated header or continuation caption'})
    title='\n'.join(p['text'] for p in good[:4])
    years=re.findall(r'в\s+(20\d{2})\s*год',title,re.I)
    if years:source['report_year']=int(years[0])
    if re.search('Министерств.{0,40}природных ресурсов',title,re.I|re.S):source['agency']='Минприроды России (по титулу PDF; официальный URL не подтверждён)'
    source['extracted_pages']=len(good)
    source['status']='processed' if len(good)==total else 'partial'
    summary={'source_id':source['id'],'sha256':source['sha256'],'version':EXTRACTOR_VERSION,'total_pages':total,'extracted_pages':len(good),
      'failed_pages':[p['page'] for p in pages if p['status']=='error'],'unrun_pages':total-len(pages),'ocr_pages':sum(p['method']=='ocr' for p in good),
      'text_pages':sum(bool(p['text'].strip()) for p in good),'review_pages':sum(bool(p['warnings']) for p in good),
      'tables':sum(len(p['tables']) for p in good),'images':sum(len(p['images']) for p in good),'numeric_candidates':sum(len(p['numbers']) for p in good),
      'continuation_candidates':continuation,'cached_pages':cached,'ocr_failures':[p['page'] for p in good if p['ocr']['status'] in ('error','missing_language')]}
    atomic_json(dest/'summary.json',summary)
    return summary

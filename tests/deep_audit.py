from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    failures=[]
    py_files=[p for p in ROOT.rglob('*.py') if '__pycache__' not in p.parts]
    for p in py_files:
        try:
            tree=ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
        except Exception as exc:
            failures.append(f'parse:{p.relative_to(ROOT)}:{type(exc).__name__}:{exc}')
            continue
        names=[]
        for n in tree.body:
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                names.append(n.name)
        seen=set()
        for name in names:
            if name in seen:
                failures.append(f'duplicate-top-level:{p.relative_to(ROOT)}:{name}')
            seen.add(name)
    pycache=[p for p in ROOT.rglob('*') if p.is_file() and (p.suffix=='.pyc' or '__pycache__' in p.parts)]
    if pycache:
        failures.append('distribution-contains-pyc:'+str(len(pycache)))
    if failures:
        print('DEEP_AUDIT_FAIL')
        for f in failures: print(f)
        raise SystemExit(1)
    print(f'DEEP_AUDIT_OK files={len(py_files)}')

if __name__ == '__main__':
    main()

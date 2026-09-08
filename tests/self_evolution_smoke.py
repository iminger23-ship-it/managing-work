from pathlib import Path
import json
import tempfile
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.self_evolution import SelfEvolution


def main():
    with tempfile.TemporaryDirectory(prefix='mylocalai-evo-smoke-') as td:
        root = Path(td) / 'project'
        data = Path(td) / 'data'
        for rel in ('core/router.py','core/context.py','core/learning.py','services/live_research.py','services/internet.py','services/knowledge.py','services/screen_observer.py','tools/registry.py'):
            src = Path(__file__).resolve().parents[1] / rel
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        for rel in ('core/__init__.py','services/__init__.py','tools/__init__.py'):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('', encoding='utf-8')

        test_src = Path(__file__).resolve().parent / 'evolution_benchmark.py'
        test_dst = root / 'tests' / 'evolution_benchmark.py'
        test_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(test_src, test_dst)

        def generate(_prompt):
            target = root / 'core/router.py'
            original = target.read_text(encoding='utf-8')
            return json.dumps({
                'summary': 'smoke test patch',
                'changes': [{'path': 'core/router.py', 'content': '# smoke-evolved\n' + original}],
                'tests': ['compileall'],
                'confidence': 1.0,
            })

        evo = SelfEvolution(str(root), str(data), generate)
        # Force a positive measured delta so this smoke test verifies the
        # generate -> validate -> benchmark -> apply -> rollback path.
        evo._benchmark_candidate = lambda _clean: (
            {'score': 99, 'max_score': 100},
            {'score': 100, 'max_score': 100},
        )
        ok, _ = evo.set_mode('autonomous')
        assert ok
        result = evo.evolve({'issues':['smoke'], 'errors':[], 'slow':[]}, autonomous=True)
        assert result['status'] == 'applied', result
        assert '# smoke-evolved' in (root / 'core/router.py').read_text(encoding='utf-8')
        ok, _ = evo.rollback()
        assert ok
        assert '# smoke-evolved' not in (root / 'core/router.py').read_text(encoding='utf-8')
    print('SELF_EVOLUTION_SMOKE_OK')


if __name__ == '__main__':
    main()

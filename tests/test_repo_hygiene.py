from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _target_recipe(makefile_text: str, target: str) -> str:
    lines = makefile_text.splitlines()
    recipe: list[str] = []
    in_target = False
    for line in lines:
        if line.startswith(f"{target}:"):
            in_target = True
            continue
        if in_target and line and not line.startswith(("\t", " ", "#")):
            break
        if in_target:
            recipe.append(line)
    return "\n".join(recipe)


class RepoHygieneTestCase(unittest.TestCase):
    def test_make_clean_preserves_versioned_scenario_markdown_reports(self) -> None:
        recipe = _target_recipe((ROOT / "Makefile").read_text(encoding="utf-8"), "clean")

        self.assertIn("rm -rf outputs/scenarios", recipe)
        self.assertIn("find reports/scenarios -type f ! -name '*.md' -delete", recipe)
        self.assertNotIn("rm -rf outputs/scenarios reports/scenarios", recipe)


if __name__ == "__main__":
    unittest.main()

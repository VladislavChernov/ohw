"""Tests for the Dz7 prompt generator (standard library only)."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import generator


class TemplateTests(unittest.TestCase):
    def test_default_input_dir_is_dz2_docs(self) -> None:
        self.assertEqual(generator.DEFAULT_DZ2_INPUT_DIR.parent.name, "dz2")
        self.assertEqual(generator.DEFAULT_DZ2_INPUT_DIR.name, "docs")

    def test_every_template_renders_without_leftover_placeholders(self) -> None:
        content = "BR-1: пользователь входит в личный кабинет\nBR-2: неверный пароль"

        for artifact_type in generator.ARTIFACT_TYPES:
            with self.subTest(artifact_type=artifact_type):
                prompt = generator.build_prompt(
                    artifact_type, content, "https://example.com", 5
                )

                self.assertEqual(generator._PLACEHOLDER_PATTERN.findall(prompt), [])
                self.assertIn("https://example.com", prompt)
                self.assertIn("BR-1", prompt)

    def test_testcase_prompt_has_valid_json_example(self) -> None:
        prompt = generator.build_prompt(
            "testcases", "BR-1: login", "https://example.com", 5
        )

        self.assertIn('{"cases": [...]}', prompt)
        self.assertNotIn('[...]}"', prompt)

    def test_matrix_prompt_says_when_no_test_cases_exist(self) -> None:
        prompt = generator.build_prompt(
            "coveragematrix", "BR-1: login", "https://example.com", 5
        )

        self.assertIn("No test cases have been generated for these requirements yet.", prompt)
        self.assertIn("Never invent", prompt)
        self.assertNotIn("TC-01", prompt)

    def test_matrix_prompt_reuses_generated_test_case_ids(self) -> None:
        prompt = generator.build_prompt(
            "coveragematrix",
            "BR-1: login",
            "https://example.com",
            5,
            testcases="- TC-01 (positive, BR-1): вход с верными данными\n"
            "- TC-02 (negative, BR-1): пустой пароль",
        )

        self.assertIn("Test cases already generated for these requirements:", prompt)
        self.assertIn("- TC-01 (positive, BR-1): вход с верными данными", prompt)
        self.assertIn("- TC-02 (negative, BR-1): пустой пароль", prompt)
        self.assertNotIn("No test cases have been generated", prompt)
        self.assertEqual(generator._PLACEHOLDER_PATTERN.findall(prompt), [])

    def test_counts_are_substituted(self) -> None:
        prompt = generator.build_prompt(
            "testcases", "BR-1: login", "https://example.com", 5
        )

        self.assertIn("Generate exactly 5 test cases: 3 positive and 2 negative.", prompt)

    def test_count_one_has_no_negative_cases(self) -> None:
        prompt = generator.build_prompt(
            "testcases", "BR-1: login", "https://example.com", 1
        )

        self.assertIn("1 test case: 1 positive and 0 negative", prompt)

    def test_requirement_rule_only_with_br_ids(self) -> None:
        with_br = generator.build_prompt("testcases", "BR-7: login", None, 5)
        without_br = generator.build_prompt("testcases", "login only", None, 5)

        self.assertIn("requirement id", with_br)
        self.assertNotIn("requirement id", without_br)

    def test_url_is_extracted_from_requirements(self) -> None:
        prompt = generator.build_prompt(
            "checklist", "Проверить https://shop.example.com/cart.", None, 5
        )

        self.assertIn("Target site: https://shop.example.com/cart", prompt)

    def test_target_site_line_is_dropped_without_url(self) -> None:
        prompt = generator.build_prompt("checklist", "no url at all", None, 5)

        self.assertNotIn("Target site", prompt)

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(generator.GeneratorError):
            generator.build_prompt("slides", "body", None, 5)

    def test_missing_template_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(
            generator.GeneratorError
        ):
            generator.load_template("checklist", Path(tmp))

    def test_unknown_placeholder_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "checklist.txt").write_text("Hello {nope}", encoding="utf-8")

            with self.assertRaises(generator.GeneratorError) as ctx:
                generator.build_prompt("checklist", "body", None, 5, directory)

            self.assertIn("nope", str(ctx.exception))

    def test_coveragematrix_requests_section_and_table(self) -> None:
        prompt = generator.build_prompt(
            "coveragematrix", "BR-1: login", None, 5
        )

        self.assertIn("## Матрица покрытия требований", prompt)
        self.assertIn("| Requirement ID |", prompt)

    def test_emit_templates_copies_all_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = generator.emit_templates(Path(tmp))

            self.assertEqual(
                sorted(path.name for path in written),
                sorted(f"{name}.txt" for name in generator.ARTIFACT_TYPES),
            )


class PresetTests(unittest.TestCase):
    def test_repository_ships_presets(self) -> None:
        presets = generator.load_presets()

        self.assertGreaterEqual(len(presets), 5)
        for preset in presets.values():
            self.assertTrue(preset.label)
            self.assertTrue(preset.text)
            self.assertTrue(preset.types)

    def test_every_shipped_preset_targets_known_types(self) -> None:
        for preset in generator.load_presets().values():
            for artifact_type in preset.types:
                self.assertIn(artifact_type, generator.ARTIFACT_TYPES)

    def test_extras_are_injected_into_extra_placeholder(self) -> None:
        prompt = generator.build_prompt(
            "testcases",
            "BR-1: login",
            None,
            5,
            extras=["Проверь пустой список и максимум."],
        )

        self.assertIn("Additional instructions:", prompt)
        self.assertIn("do not add pages", prompt)
        self.assertIn("- Проверь пустой список и максимум.", prompt)

    def test_no_extras_means_no_empty_block(self) -> None:
        prompt = generator.build_prompt("checklist", "BR-1: login", None, 5)

        self.assertNotIn("Additional instructions:", prompt)

    def test_select_extras_rejects_unknown_preset(self) -> None:
        with self.assertRaises(generator.GeneratorError) as ctx:
            generator.select_extras({}, ["nope"], "testcases")

        self.assertIn("unknown preset", str(ctx.exception))

    def test_select_extras_rejects_wrong_type(self) -> None:
        presets = generator.load_presets()

        with self.assertRaises(generator.GeneratorError) as ctx:
            generator.select_extras(presets, ["localization"], "coveragematrix")

        self.assertIn("does not apply", str(ctx.exception))

    def test_preset_file_id_must_match_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "wrong-name.toml").write_text(
                'id = "other"\nlabel = "L"\ntext = "T"\n', encoding="utf-8"
            )

            with self.assertRaises(generator.GeneratorError) as ctx:
                generator.load_presets(directory)

            self.assertIn("must match", str(ctx.exception))

    def test_preset_with_unknown_type_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "bad.toml").write_text(
                'id = "bad"\nlabel = "L"\ntext = "T"\ntypes = ["slides"]\n',
                encoding="utf-8",
            )

            with self.assertRaises(generator.GeneratorError):
                generator.load_presets(directory)

    def test_preset_without_extra_placeholder_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "checklist.txt").write_text(
                "No extras here\n{content}", encoding="utf-8"
            )

            with self.assertRaises(generator.GeneratorError) as ctx:
                generator.build_prompt(
                    "checklist",
                    "body",
                    None,
                    5,
                    directory,
                    extras=["extra text"],
                )

            self.assertIn("{extra}", str(ctx.exception))

    def test_emit_presets_copies_toml_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = generator.emit_presets(Path(tmp))

            self.assertEqual(len(written), len(generator.load_presets()))
            self.assertEqual(len(list(Path(tmp).glob("*.toml"))), len(written))


class CliTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = generator.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_single_type_prints_prompt(self) -> None:
        code, out, _ = self.run_main(
            ["--requirements", "BR-1: login https://example.com", "--type", "testcase"]
        )

        self.assertEqual(code, generator.EXIT_OK)
        self.assertIn("Generate exactly 5 test cases", out)
        self.assertIn("https://example.com", out)

    def test_single_type_saves_to_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "prompt.txt"
            code, out, _ = self.run_main(
                [
                    "--requirements",
                    "BR-1: login",
                    "--type",
                    "checklist",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(code, generator.EXIT_OK)
            self.assertTrue(output.is_file())
            self.assertIn(
                "Create a test checklist", output.read_text(encoding="utf-8")
            )
            self.assertIn("prompt saved to:", out)

    def test_all_types_writes_five_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = self.run_main(
                [
                    "--requirements",
                    "BR-1: login",
                    "--all-types",
                    "--output-dir",
                    tmp,
                ]
            )

            self.assertEqual(code, generator.EXIT_OK)
            self.assertEqual(
                sorted(path.name for path in Path(tmp).glob("*.txt")),
                sorted(f"{name}.txt" for name in generator.ARTIFACT_TYPES),
            )

    def test_emit_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = self.run_main(["--emit-templates", "--output-dir", tmp])

            self.assertEqual(code, generator.EXIT_OK)
            self.assertEqual(len(list(Path(tmp).glob("*.txt"))), 5)

    def test_emit_presets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = self.run_main(["--emit-presets", "--output-dir", tmp])

            self.assertEqual(code, generator.EXIT_OK)
            self.assertEqual(
                len(list(Path(tmp).glob("*.toml"))),
                len(generator.load_presets()),
            )

    def test_preset_is_appended_to_prompt(self) -> None:
        code, out, _ = self.run_main(
            [
                "--requirements",
                "BR-1: login",
                "--type",
                "testcases",
                "--preset",
                "negative-scenarios",
            ]
        )

        self.assertEqual(code, generator.EXIT_OK)
        self.assertIn("Additional instructions:", out)
        self.assertIn("Cover failure paths explicitly", out)

    def test_unknown_preset_returns_usage_error(self) -> None:
        code, _, err = self.run_main(
            ["--requirements", "BR-1: login", "--preset", "nope"]
        )

        self.assertEqual(code, generator.EXIT_USAGE)
        self.assertIn("unknown preset", err)

    def test_list_presets(self) -> None:
        code, out, _ = self.run_main(["--list-presets"])

        self.assertEqual(code, generator.EXIT_OK)
        self.assertIn("boundary-values:", out)
        self.assertIn("Граничные значения", out)

    def test_requirements_file_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "req.md"
            requirements.write_text("BR-1: оплата картой", encoding="utf-8")

            code, out, _ = self.run_main(
                ["--requirements-file", str(requirements), "--type", "testplan"]
            )

            self.assertEqual(code, generator.EXIT_OK)
            self.assertIn("оплата картой", out)

    def test_requirements_file_name_is_resolved_in_input_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "login.md").write_text(
                "BR-1: вход из входной директории Dz2", encoding="utf-8"
            )

            code, out, _ = self.run_main(
                [
                    "--requirements-file",
                    "login.md",
                    "--input-dir",
                    tmp,
                    "--type",
                    "checklist",
                ]
            )

            self.assertEqual(code, generator.EXIT_OK)
            self.assertIn("вход из входной директории Dz2", out)

    def test_missing_requirements_returns_usage_error(self) -> None:
        code, _, err = self.run_main(["--type", "testplan"])

        self.assertEqual(code, generator.EXIT_USAGE)
        self.assertIn("requirements are required", err)

    def test_missing_requirements_file_returns_usage_error(self) -> None:
        code, _, err = self.run_main(
            ["--requirements-file", "C:/definitely/missing/req.md"]
        )

        self.assertEqual(code, generator.EXIT_USAGE)
        self.assertIn("not found", err)

    def test_config_sets_count_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text(
                "[generation]\ndefault_count = 7\ndefault_type = \"testcases\"\n",
                encoding="utf-8",
            )

            code, out, _ = self.run_main(
                ["--requirements", "BR-1: login", "--config", str(config)]
            )

            self.assertEqual(code, generator.EXIT_OK)
            self.assertIn("exactly 7 test cases", out)

    def test_config_templates_dir_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            templates.mkdir()
            for name in generator.ARTIFACT_TYPES:
                (templates / f"{name}.txt").write_text(
                    f"CUSTOM {name} TEMPLATE\n{{content}}", encoding="utf-8"
                )
            config = Path(tmp) / "config.toml"
            config.write_text(
                '[paths]\ntemplates_dir = "templates"\n', encoding="utf-8"
            )

            code, out, _ = self.run_main(
                [
                    "--requirements",
                    "BR-1: login",
                    "--type",
                    "qareport",
                    "--config",
                    str(config),
                ]
            )

            self.assertEqual(code, generator.EXIT_OK)
            self.assertIn("CUSTOM qareport TEMPLATE", out)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shortly.apps.personal.automation.capcut_assets import (
    PreparedExport,
    commit_export,
    load_prepared_export,
    prepare_export,
    validate_capcut_project,
)
from shortly.apps.personal.automation.review import (
    qwen_read_only_review,
    technical_review,
)
from shortly.apps.personal.automation.store import AutomationStore
from shortly.core.domain.assets import (
    AssetCandidate,
    AssetType,
    CandidateStatus,
    MediaKind,
)
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest
from shortly.generate.capcut_export import (
    _attach_common_mask,
    _body_start_after_intro_click,
    _build_caption_seg,
    _build_photo_segment,
    _caption_pages,
    _caption_safe_korean,
    _crop_generated_image_safe_top,
    _ensure_intro_start_sfx,
    _find_reference_text_template,
    _intro_timing,
    _place_intro_transition_click,
    _plan_sentences,
    _tts_pronunciation_text,
    prepare_guide_tts_timing,
    _reference_asset_layout,
    _retime_body_title_placeholders,
    _set_single_line_title_keep_style,
    _split_sentences,
)
from shortly.llm.base import (
    LLMClient,
    LLMError,
    LLMResponseParseError,
)


class _ReviewLlm(LLMClient):
    available = True
    provider = "local"

    def __init__(self):
        super().__init__("qwen-fixture", "qwen-fixture")

    def complete_json(self, *args, **kwargs):
        return {"passed": True, "summary": "ok", "findings": []}


class _FailedReviewLlm(_ReviewLlm):
    def complete_json(self, *args, **kwargs):
        raise LLMError("connection refused")


class _MalformedReviewLlm(_ReviewLlm):
    def complete_json(self, *args, **kwargs):
        raise LLMResponseParseError("invalid json")


class _AutoStartFailedReviewLlm(_FailedReviewLlm):
    _auto_start = True


class PersonalReviewAndExportTests(unittest.TestCase):
    def test_asset_handoff_replaces_estimates_with_measured_tts_timing(
        self,
    ) -> None:
        class Config:
            enabled = True
            voice_id = "voice"
            model = "model"
            language = "kor"
            emotion_preset = "toneup"
            emotion_intensity = 1.0
            tempo = 1.3

        guide = {
            "title": "fixture",
            "total_duration_sec": 8.0,
            "segments": [
                {
                    "index": 0,
                    "start_sec": 0.0,
                    "duration_sec": 4.0,
                    "narration": "첫 번째 대사",
                    "tts": {},
                },
                {
                    "index": 1,
                    "start_sec": 4.0,
                    "duration_sec": 4.0,
                    "narration": "두 번째 대사",
                    "tts": {},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "shortly.tts.synth_to_file",
                side_effect=[2_300_000, 1_000_000],
            ):
                measured, report = prepare_guide_tts_timing(
                    guide,
                    Config(),
                    temporary,
                )

        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["segments"], 2)
        self.assertEqual(measured["segments"][0]["duration_sec"], 2.3)
        self.assertEqual(measured["segments"][1]["start_sec"], 2.39)
        self.assertEqual(measured["segments"][1]["duration_sec"], 1.0)
        self.assertEqual(measured["total_duration_sec"], 3.39)
        self.assertEqual(
            measured["segments"][0]["timing_source"],
            "typecast_tts",
        )

    def test_body_title_has_no_manual_break_or_font_shrink(self) -> None:
        material = {
            "font_size": 12.0,
            "content": json.dumps(
                {
                    "text": "제목이 들어갑니다",
                    "styles": [{"range": [0, 9], "size": 12.0}],
                },
                ensure_ascii=False,
            ),
        }

        _set_single_line_title_keep_style(
            material,
            "독도 때문에 일본 시장\n포기한 회사2",
        )

        content = json.loads(material["content"])
        self.assertNotIn("\n", content["text"])
        self.assertEqual(content["text"], "독도 때문에 일본 시장 포기한 회사2")
        self.assertEqual(material["font_size"], 12.0)
        self.assertEqual(content["styles"][0]["size"], material["font_size"])
        self.assertEqual(material["line_feed"], 1)
        self.assertFalse(material["oneline_cutoff"])
        self.assertTrue(material["force_apply_line_max_width"])

    def test_long_body_title_preserves_existing_native_text_box(self) -> None:
        material = {
            "font_size": 12.08, "fixed_width": 747.6, "alignment": 0,
            "line_max_width": .82, "force_apply_line_max_width": False,
            "content": json.dumps({"text": "제목", "styles": [{"range": [0, 2], "size": 12.08}]}),
        }
        text = "한 줄을 넘길 만큼 긴 제목도 가운데에서 의도적으로 둘로 나누지 않는 본문 제목"
        _set_single_line_title_keep_style(material, text)
        once = json.dumps(material, sort_keys=True)
        _set_single_line_title_keep_style(material, text)
        self.assertEqual(json.dumps(material, sort_keys=True), once)
        self.assertEqual(json.loads(material["content"])["text"], text)
        self.assertEqual(material["font_size"], 12.08)
        self.assertEqual(material["fixed_width"], 747.6)
        self.assertEqual(material["alignment"], 0)
        self.assertFalse(material["force_apply_line_max_width"])

    def test_generated_video_uses_a_real_common_mask_material(self) -> None:
        segment = {"extra_material_refs": [], "enable_video_mask": False}
        cloned_materials = {}
        template = {
            "id": "source-mask",
            "type": "mask",
            "resource_type": "mirror",
            "config": {"centerY": -0.35, "height": 1.16},
        }

        self.assertTrue(
            _attach_common_mask(segment, cloned_materials, template)
        )

        mask = cloned_materials["common_mask"][0]
        self.assertNotEqual(mask["id"], "source-mask")
        self.assertEqual(segment["extra_material_refs"], [mask["id"]])
        self.assertTrue(segment["enable_video_mask"])

    def test_generated_image_safe_top_is_cropped_before_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "generated.png"

            from PIL import Image

            Image.new("RGB", (200, 300), (240, 240, 240)).save(path)
            self.assertTrue(_crop_generated_image_safe_top(path))
            with Image.open(path) as image:
                self.assertEqual(image.size, (200, 225))

            self.assertEqual(_reference_asset_layout(200, 225), (0.86, -0.18))

    def test_exported_caption_always_uses_capcut_line_spacing_two(self) -> None:
        template = {
            "id": "segment",
            "material_id": "text-material",
            "extra_material_refs": [],
        }
        materials = {
            "texts": [
                {
                    "id": "text-material",
                    "content": '{"text":"자막 예시","styles":[]}',
                    "line_spacing": 0.02,
                }
            ]
        }

        _segment, cloned = _build_caption_seg(
            template,
            materials,
            "한 줄 자막",
            0,
            1_000_000,
            0.5,
            1,
            line_spacing=0.12,
        )

        self.assertEqual(cloned["texts"][0]["line_spacing"], 0.12)

    def test_qwen_review_returns_report_without_mutating_manifest(self) -> None:
        manifest = build_cut_manifest(
            {"title": "fixture", "segments": [{"duration_sec": 1}]}
        )
        before = manifest.to_dict()
        report = qwen_read_only_review(
            _ReviewLlm(), manifest, technical=technical_review(manifest)
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.reviewer, "local-qwen:qwen-fixture")
        self.assertEqual(manifest.to_dict(), before)

    def test_qwen_review_distinguishes_request_and_parse_failures(self) -> None:
        manifest = build_cut_manifest(
            {"title": "fixture", "segments": [{"duration_sec": 1}]}
        )

        request_failure = qwen_read_only_review(
            _FailedReviewLlm(),
            manifest,
        )
        parse_failure = qwen_read_only_review(
            _MalformedReviewLlm(),
            manifest,
        )

        self.assertEqual(
            request_failure.findings[0].code,
            "local_model_request_failed",
        )
        self.assertIn("LM Studio", request_failure.findings[0].message)
        auto_start_failure = qwen_read_only_review(
            _AutoStartFailedReviewLlm(),
            manifest,
        )
        self.assertIn("자동 시작", auto_start_failure.findings[0].message)
        self.assertEqual(
            parse_failure.findings[0].code,
            "review_parse_failed",
        )

    def test_prepare_export_validates_selected_assets_without_capcut_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AutomationStore(root / "automation.sqlite3")
            manifest = build_cut_manifest(
                {
                    "title": "fixture",
                    "segments": [
                        {
                            "index": 0,
                            "duration_sec": 1,
                            "image_suggestions": ["생성 일러스트"],
                            "narration": "확인용 대사였음.",
                        }
                    ],
                }
            )
            asset = root / "asset.jpg"
            asset.write_bytes(b"fixture")
            cut = manifest.cuts[0]
            cut.asset_type = AssetType.GENERATED_IMAGE
            cut.disclosure_text = (
                "이 이미지는 이해를 돕기 위한 AI 생성\n"
                "이미지이며, 실존 인물이 아닙니다."
            )
            candidate = AssetCandidate(
                provider="fake",
                model="fake",
                media_kind=MediaKind.IMAGE,
                status=CandidateStatus.SELECTED,
                prepared_path=str(asset),
            )
            cut.candidates.append(candidate)
            cut.selected_candidate_id = candidate.id
            store.save_manifest(manifest)
            capcut = root / "capcut"
            capcut.mkdir()

            with patch(
                "shortly.apps.personal.automation.capcut_assets.find_capcut_draft_root",
                return_value=capcut,
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._find_template_project",
                return_value=capcut / "template",
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._pick_templates",
                return_value=({}, {}, {}),
            ):
                prepared = prepare_export(
                    store=store,
                    manifest_id=manifest.manifest_id,
                    guide={"title": "fixture", "segments": [{"index": 0, "duration_sec": 1,
                        "image_suggestions": ["생성 일러스트"], "narration": "확인용 대사였음."}]},
                    staging_root=root / "staging",
                )

            self.assertTrue(Path(prepared.plan_path).is_file())
            self.assertEqual(
                prepared.cut_assets["s000-c00"]["candidate_id"], candidate.id
            )
            self.assertEqual(
                prepared.cut_assets["s000-c00"]["asset_type"],
                AssetType.GENERATED_IMAGE.value,
            )
            self.assertEqual(
                prepared.cut_assets["s000-c00"]["path"], str(asset.resolve())
            )
            self.assertIn(
                "실존 인물이 아닙니다",
                prepared.cut_assets["s000-c00"]["disclosure_text"],
            )
            self.assertEqual(list(capcut.iterdir()), [])

    def test_ai_person_disclosure_style_is_found_in_manual_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "사실 너만 모르던 스테디셀러 떡"
            reference.mkdir()
            material_id = "disclosure-material"
            (reference / "draft_content.json").write_text(
                __import__("json").dumps(
                    {
                        "materials": {
                            "texts": [
                                {
                                    "id": material_id,
                                    "type": "text",
                                    "content": __import__("json").dumps(
                                        {
                                            "text": (
                                                "이 이미지는 이해를 돕기 위한 AI 생성\n"
                                                "이미지이며, 실존 인물이 아닙니다."
                                            )
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ]
                        },
                        "tracks": [
                            {
                                "type": "text",
                                "segments": [
                                    {
                                        "material_id": material_id,
                                        "target_timerange": {
                                            "start": 0,
                                            "duration": 1_000_000,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            template = _find_reference_text_template(
                root,
                ("AI 생성", "실존 인물이 아닙니다"),
            )

        self.assertIsNotNone(template)
        self.assertEqual(template["project"], reference.name)

    def test_prepare_export_allows_unselected_stock_cut(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AutomationStore(root / "automation.sqlite3")
            manifest = build_cut_manifest(
                {
                    "title": "fixture",
                    "segments": [
                        {
                            "index": 0,
                            "duration_sec": 1,
                            "image_suggestions": ["시장 자료 사진"],
                            "narration": "확인용 대사였음.",
                        }
                    ],
                }
            )
            cut = manifest.cuts[0]
            cut.asset_type = AssetType.STOCK_IMAGE
            store.save_manifest(manifest)
            capcut = root / "capcut"
            capcut.mkdir()

            with patch(
                "shortly.apps.personal.automation.capcut_assets.find_capcut_draft_root",
                return_value=capcut,
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._find_template_project",
                return_value=capcut / "template",
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._pick_templates",
                return_value=({}, {}, {}),
            ):
                prepared = prepare_export(
                    store=store,
                    manifest_id=manifest.manifest_id,
                    guide={"title": "fixture", "segments": [{"index": 0, "duration_sec": 1,
                        "image_suggestions": ["시장 자료 사진"], "narration": "확인용 대사였음."}]},
                    staging_root=root / "staging",
                )

            self.assertEqual(prepared.cut_assets, {})
            self.assertTrue(Path(prepared.plan_path).is_file())

    def test_prepare_export_still_blocks_unselected_generated_cut(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AutomationStore(root / "automation.sqlite3")
            manifest = build_cut_manifest(
                {
                    "title": "fixture",
                    "segments": [
                        {
                            "index": 0,
                            "duration_sec": 1,
                            "image_suggestions": ["가상의 제품 연출 장면"],
                        }
                    ],
                }
            )
            cut = manifest.cuts[0]
            cut.asset_type = AssetType.GENERATED_IMAGE
            store.save_manifest(manifest)
            capcut = root / "capcut"
            capcut.mkdir()

            with patch(
                "shortly.apps.personal.automation.capcut_assets.find_capcut_draft_root",
                return_value=capcut,
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._find_template_project",
                return_value=capcut / "template",
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._pick_templates",
                return_value=({}, {}, {}),
            ):
                with self.assertRaisesRegex(ValueError, cut.id):
                    prepare_export(
                        store=store,
                        manifest_id=manifest.manifest_id,
                        guide={"title": "fixture", "segments": [{"index": 0, "duration_sec": 1,
                            "image_suggestions": ["가상의 제품 연출 장면"]}]},
                        staging_root=root / "staging",
                    )

    def test_commit_export_rejects_old_unverified_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "asset.png"
            asset.write_bytes(b"fixture")
            prepared = PreparedExport(
                id="export",
                manifest_id="manifest",
                root=str(root),
                guide={"title": "fixture", "segments": []},
                cut_assets={
                    "s000-c00": {
                        "path": str(asset),
                        "media_kind": "image",
                        "candidate_id": "candidate",
                    }
                },
                plan_path=str(root / "prepared-export.json"),
                template_name="챙맛 템플릿",
            )
            expected = {"ok": False, "reason": "fixture"}

            with patch(
                "shortly.apps.personal.automation.capcut_assets.export_via_template",
                return_value=expected,
            ) as export:
                with self.assertRaisesRegex(ValueError, "준비"):
                    commit_export(prepared, tts_cfg="tts-config", tts_out_dir="tts-output")
                export.assert_not_called()

    def test_legacy_prepared_image_is_upgraded_to_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared_image = root / "prepared-1080x1920.jpg"
            original_image = root / "image-original.png"
            prepared_image.write_bytes(b"prepared")
            original_image.write_bytes(b"original")
            plan = root / "prepared-export.json"
            plan.write_text(
                __import__("json").dumps(
                    {
                        "id": "export",
                        "manifest_id": "manifest",
                        "root": str(root),
                        "guide": {},
                        "cut_assets": {
                            "s000-c00": {
                                "path": str(prepared_image),
                                "media_kind": "image",
                                "candidate_id": "candidate",
                            }
                        },
                        "plan_path": str(plan),
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_prepared_export(plan)

        self.assertEqual(
            loaded.cut_assets["s000-c00"]["path"],
            str(original_image.resolve()),
        )

    def test_reference_layout_contains_images_and_videos_in_same_body(self) -> None:
        self.assertAlmostEqual(_reference_asset_layout(1024, 1536)[0], 0.58 * 1920 / 1620)
        self.assertEqual(
            _reference_asset_layout(1080, 1920, "video"),
            (0.58, -0.18),
        )
        self.assertEqual(
            _reference_asset_layout(1920, 1080, "video"),
            (0.86, -0.18),
        )
        self.assertAlmostEqual(_reference_asset_layout(1000, 1000)[0], 0.86)
        self.assertEqual(_reference_asset_layout(1280, 720), (0.86, -0.18))

    def test_caption_planner_preserves_quoted_sentence(self) -> None:
        text = "사실 처음 먹어보면 '어? 이거 어디서 먹어본 맛인데?' 싶다고"

        self.assertEqual(_split_sentences(text), [text])
        pages = _caption_pages(text)

        self.assertTrue(
            any(
                "'어? 이거 어디서 먹어본 맛인데?'" in line
                for page in pages
                for line in page
            )
        )

    def test_two_line_caption_accumulates_with_tts_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = _plan_sentences(
                [
                    {
                        "narration": (
                            "요즘 난리난 상하이 버터떡, "
                            "언론이 억지로 밀어버린 거라고 함"
                        )
                    }
                ],
                None,
                Path(temporary),
            )

        first, second = plan["captions"][:2]
        self.assertTrue(first["text"].endswith("\n"))
        self.assertTrue(second["text"].startswith("\n"))
        self.assertEqual(first["layer"], 0)
        self.assertEqual(second["layer"], 1)
        self.assertGreater(second["start"], first["start"])
        self.assertEqual(
            first["start"] + first["dur"],
            second["start"] + second["dur"],
        )

    def test_caption_pages_follow_punctuation_and_balance_lines(self) -> None:
        pages = _caption_pages(
            "지도에서 섬 하나만 지우면 일본 시장이 열리는데, "
            "그걸 걷어차버린 한국 제품이 있다고"
        )

        self.assertEqual(
            pages,
            [
                [
                    "지도에서 섬 하나만 지우면",
                    "일본 시장이 열리는데",
                ],
                ["그걸 걷어차버린 한국 제품이 있다고"],
            ],
        )

    def test_caption_pages_do_not_leave_one_word_orphan(self) -> None:
        self.assertEqual(
            _caption_pages("회사가 대기업 인수 검토설까지 나왔다는 거"),
            [["회사가 대기업 인수 검토설까지 나왔다는 거"]],
        )

    def test_caption_pages_pair_short_comma_clauses(self) -> None:
        self.assertEqual(
            _caption_pages(
                "요즘 난리난 상하이 버터떡, "
                "언론이 억지로 밀어버린 거라고 함"
            ),
            [[
                "요즘 난리난 상하이 버터떡",
                "언론이 억지로 밀어버린 거라고 함",
            ]],
        )

    def test_caption_removes_period_and_all_commas(self) -> None:
        self.assertEqual(
            _caption_pages("사람이 밥을 먹었음."),
            [["사람이 밥을 먹었음"]],
        )
        self.assertEqual(
            _caption_pages("사람이 밥을 먹었는데,"),
            [["사람이 밥을 먹었는데"]],
        )
        self.assertEqual(
            _caption_pages("사람이 밥을 먹었는데, 먹고 탈이 났다고 함."),
            [["사람이 밥을 먹었는데", "먹고 탈이 났다고 함"]],
        )

    def test_caption_punctuation_cleanup_does_not_change_tts_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = _plan_sentences(
                [{"narration": "사람이 밥을 먹었는데, 먹고 탈이 났다고 함."}],
                None,
                Path(temporary),
            )

        self.assertEqual(
            plan["audios"][0]["text"],
            "사람이 밥을 먹었는데, 먹고 탈이 났다고 함.",
        )
        self.assertTrue(all("." not in item["text"] for item in plan["captions"]))
        self.assertTrue(all("," not in item["text"] for item in plan["captions"]))

    def test_display_removes_internal_fullwidth_and_numeric_commas(self) -> None:
        from shortly.generate.capcut_export import _caption_display_lines
        original = ["처음, 중간，끝,", "하루 1,500통, 3.5배."]
        self.assertEqual(
            _caption_display_lines(original),
            ["처음 중간끝", "하루 1500통 3.5배"],
        )
        self.assertEqual(original, ["처음, 중간，끝,", "하루 1,500통, 3.5배."])

    def test_caption_preserves_decimal_point(self) -> None:
        self.assertEqual(
            _caption_pages("당류가 3.5그램이었음."),
            [["당류가 3.5그램이었음"]],
        )
        self.assertEqual(
            _split_sentences("가격은 3.99달러였음. 바로 품절됐음."),
            ["가격은 3.99달러였음.", "바로 품절됐음."],
        )

    def test_cached_caption_is_cleaned_at_render_boundary(self) -> None:
        material = {"id": "caption", "type": "text", "content": "{}"}
        with patch(
            "shortly.generate.capcut_export._clone_seg",
            return_value=({}, {"texts": [material]}, material),
        ), patch("shortly.generate.capcut_export._set_text_keep_style") as setter:
            _build_caption_seg({}, {}, "\n하루 1,500통,", 0, 1000000, None, 20000)
        setter.assert_called_once_with(material, "\n하루 1500통")

    def test_tts_pronounces_decimal_digits_without_changing_caption(self) -> None:
        self.assertEqual(
            _tts_pronunciation_text("가격은 3.99달러였음."),
            "가격은 삼쩜 구구 달러였음.",
        )
        self.assertEqual(
            _tts_pronunciation_text("당류 12.5그램, 비율 0.75퍼센트"),
            "당류 십이쩜 오 그램, 비율 영쩜 칠오 퍼센트",
        )

        with tempfile.TemporaryDirectory() as temporary:
            plan = _plan_sentences(
                [{"narration": "가격은 3.99달러였음."}],
                None,
                Path(temporary),
            )

        self.assertEqual(plan["audios"][0]["text"], "가격은 3.99달러였음.")
        self.assertEqual(
            plan["audios"][0]["tts_text"],
            "가격은 삼쩜 구구 달러였음.",
        )
        self.assertEqual(plan["captions"][0]["text"], "가격은 3.99달러였음")

    def test_caption_safe_korean_converts_known_and_removes_unknown_hanja(self) -> None:
        sanitized = _caption_safe_korean("日 수출과 龍 문양")

        self.assertEqual(sanitized, "일본 수출과 문양")
        self.assertNotRegex(
            sanitized,
            r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
        )

    def test_caption_plan_sanitizes_hanja_before_tts_and_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = _plan_sentences(
                [{"narration": "독도 포장지로 日 수출이 막혔다고"}],
                None,
                Path(temporary),
            )

        self.assertEqual(
            plan["audios"][0]["text"],
            "독도 포장지로 일본 수출이 막혔다고",
        )
        rendered = " ".join(item["text"] for item in plan["captions"])
        self.assertIn("일본 수출", rendered)
        self.assertNotRegex(
            rendered,
            r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
        )

    def test_intro_timing_keeps_start_sfx_and_title_tts_separate(self) -> None:
        title_start, intro_end = _intro_timing(
            1_000_000,
            1_700_000,
            733_333,
        )

        self.assertEqual(title_start, 733_333)
        self.assertEqual(intro_end, 2_433_333)

    def test_intro_sfx_and_transition_click_follow_reference_timing(self) -> None:
        start_material = {
            "id": "start-sfx",
            "type": "extract_music",
            "name": "28. 띠링 - 시작음.mp3",
            "path": "start.mp3",
        }
        document = {
            "materials": {"audios": [start_material]},
            "tracks": [
                {
                    "type": "audio",
                    "segments": [
                        {
                            "id": "start-segment",
                            "material_id": "start-sfx",
                            "extra_material_refs": [],
                            "target_timerange": {
                                "start": 500_000,
                                "duration": 733_333,
                            },
                            "source_timerange": {
                                "start": 0,
                                "duration": 733_333,
                            },
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "장난감이 소금빵이 돼버린 왁뿌볼"
            reference.mkdir()
            (reference / "draft_content.json").write_text(
                __import__("json").dumps(
                    {
                        "materials": {
                            "audios": [
                                {
                                    "id": "click-sfx",
                                    "type": "extract_music",
                                    "name": "01. 클릭음.mp3",
                                    "path": "click.mp3",
                                }
                            ]
                        },
                        "tracks": [
                            {
                                "type": "audio",
                                "segments": [
                                    {
                                        "id": "click-segment",
                                        "material_id": "click-sfx",
                                        "extra_material_refs": [],
                                        "target_timerange": {
                                            "start": 1_566_666,
                                            "duration": 266_667,
                                        },
                                        "source_timerange": {
                                            "start": 0,
                                            "duration": 266_667,
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            start = _ensure_intro_start_sfx(document, root)
            click = _place_intro_transition_click(
                document,
                root,
                2_433_333,
            )

        self.assertTrue(start["present"])
        self.assertFalse(start["added"])
        self.assertEqual(start["end"], 733_333)
        self.assertEqual(
            document["tracks"][0]["segments"][0]["target_timerange"]["start"],
            0,
        )
        self.assertTrue(click["present"])
        self.assertTrue(click["added"])
        self.assertEqual(click["start"], 2_333_333)
        self.assertEqual(click["end"], 2_600_000)
        self.assertEqual(
            document["tracks"][-1]["segments"][0]["target_timerange"],
            {"start": 2_333_333, "duration": 266_667},
        )

    def test_intro_click_finishes_before_body_tts_starts(self) -> None:
        document = {"materials": {"audios": []}, "tracks": []}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "장난감이 소금빵이 돼버린 왁뿌볼"
            reference.mkdir()
            (reference / "draft_content.json").write_text(
                __import__("json").dumps(
                    {
                        "materials": {
                            "audios": [
                                {
                                    "id": "click-sfx",
                                    "type": "extract_music",
                                    "name": "01. 클릭음.mp3",
                                    "path": "click.mp3",
                                }
                            ]
                        },
                        "tracks": [
                            {
                                "type": "audio",
                                "segments": [
                                    {
                                        "id": "click-segment",
                                        "material_id": "click-sfx",
                                        "extra_material_refs": [],
                                        "target_timerange": {
                                            "start": 2_033_333,
                                            "duration": 266_667,
                                        },
                                        "source_timerange": {
                                            "start": 0,
                                            "duration": 266_667,
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            title_end = 2_133_333
            click = _place_intro_transition_click(document, root, title_end)
            body_start = _body_start_after_intro_click(title_end, click)

        self.assertEqual(click["start"], 2_033_333)
        self.assertEqual(click["end"], 2_300_000)
        self.assertEqual(body_start, 2_600_000)
        self.assertEqual(body_start - click["end"], 300_000)

    def test_missing_intro_click_adds_no_pause(self) -> None:
        self.assertEqual(_body_start_after_intro_click(2_000_000, {"end": None}), 2_000_000)
        self.assertEqual(_body_start_after_intro_click(3_000_000, {"end": 2_000_000}), 3_000_000)

    def test_body_title_starts_at_first_cut_and_ends_with_video(self) -> None:
        body_material = {
            "id": "body-title",
            "content": __import__("json").dumps(
                {"text": "제목이 들어갑니다", "styles": []}
            ),
        }
        document = {
            "materials": {"texts": [body_material]},
            "tracks": [
                {
                    "type": "text",
                    "segments": [
                        {
                            "material_id": "body-title",
                            "target_timerange": {
                                "start": 1_000_000,
                                "duration": 9_000_000,
                            },
                        }
                    ],
                }
            ],
        }

        changed = _retime_body_title_placeholders(
            document,
            2_433_333,
            12_000_000,
        )

        self.assertEqual(changed, 1)
        self.assertEqual(
            document["tracks"][0]["segments"][0]["target_timerange"],
            {"start": 2_433_333, "duration": 9_566_667},
        )

    def test_capcut_segment_can_reference_generated_video(self) -> None:
        template = {
            "seg": {
                "id": "segment",
                "material_id": "material",
                "extra_material_refs": [],
                "volume": 1.0,
                "last_nonzero_volume": 1.0,
            },
            "materials": {
                "videos": [{"id": "material", "type": "photo", "path": ""}]
            },
        }
        segment, cloned = _build_photo_segment(
            template,
            Path("fixture.mp4"),
            0,
            1_000_000,
            1.0,
            0.0,
            1,
            media_kind="video",
        )
        material = cloned["videos"][0]
        self.assertEqual(material["type"], "video")
        self.assertEqual(material["duration"], 1_000_000)
        self.assertEqual(segment["source_timerange"]["duration"], 1_000_000)
        self.assertEqual(segment["volume"], 0.0)
        self.assertEqual(segment["last_nonzero_volume"], 0.0)

    def test_generated_video_dimensions_and_full_original_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.mp4"
            tkhd_payload = (
                b"\0" * 16
                + (1920 << 16).to_bytes(4, "big")
                + (1080 << 16).to_bytes(4, "big")
            )
            path.write_bytes(
                (8 + len(tkhd_payload)).to_bytes(4, "big")
                + b"tkhd"
                + tkhd_payload
            )
            template = {
                "seg": {
                    "id": "segment",
                    "material_id": "material",
                    "extra_material_refs": [],
                },
                "materials": {
                    "videos": [
                        {"id": "material", "type": "photo", "path": ""}
                    ]
                },
            }

            _segment, cloned = _build_photo_segment(
                template,
                path,
                0,
                1_000_000,
                0.64,
                -0.21,
                1,
                media_kind="video",
            )

            material = cloned["videos"][0]
            self.assertEqual((material["width"], material["height"]), (1920, 1080))
            self.assertEqual(material["crop"]["upper_left_x"], 0.0)
            self.assertEqual(material["crop"]["upper_right_x"], 1.0)

    def test_committed_capcut_project_validation_resolves_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            cuts = project / "shortly_cuts"
            cuts.mkdir()
            asset = cuts / "cut_01.jpg"
            asset.write_bytes(b"fixture")
            document = {
                "duration": 1_000_000,
                "canvas_config": {
                    "ratio": "original",
                    "width": 1080,
                    "height": 1920,
                },
                "materials": {
                    "videos": [
                        {
                            "id": "material",
                            "type": "photo",
                            "path": str(asset),
                            "width": 1080,
                            "height": 1920,
                        }
                    ]
                },
                "tracks": [
                    {
                        "type": "video",
                        "segments": [{"material_id": "material"}],
                    }
                ],
            }
            (project / "draft_content.json").write_text(
                __import__("json").dumps(document),
                encoding="utf-8",
            )

            result = validate_capcut_project(
                project, expected_asset_count=1
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["inserted_assets"][0]["type"], "photo")

    def test_committed_capcut_project_validation_reports_missing_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "draft_content.json").write_text(
                __import__("json").dumps(
                    {
                        "duration": 1,
                        "canvas_config": {"ratio": "9:16"},
                        "materials": {},
                        "tracks": [
                            {
                                "segments": [
                                    {"material_id": "missing-material"}
                                ]
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_capcut_project(project)

        self.assertFalse(result["passed"])
        self.assertIn("material references", result["errors"][0])

    def test_committed_capcut_project_validation_allows_unmasked_contain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            cuts = project / "shortly_cuts"
            cuts.mkdir()
            first = cuts / "cut_01.mp4"
            second = cuts / "cut_02.png"
            for path in (first, second):
                path.write_bytes(b"fixture")
            document = {
                "duration": 2_000_000,
                "canvas_config": {"ratio": "9:16"},
                "materials": {
                    "videos": [
                        {"id": "first", "type": "video", "path": str(first)},
                        {"id": "second", "type": "photo", "path": str(second)},
                    ]
                },
                "tracks": [
                    {
                        "type": "video",
                        "segments": [
                            {
                                "material_id": "first",
                                "volume": 0.0,
                                "target_timerange": {
                                    "start": 0,
                                    "duration": 1_000_000,
                                },
                            },
                            {
                                "material_id": "second",
                                "target_timerange": {
                                    "start": 1_000_000,
                                    "duration": 1_000_000,
                                },
                            },
                        ],
                    },
                ],
            }
            (project / "draft_content.json").write_text(
                __import__("json").dumps(document),
                encoding="utf-8",
            )

            result = validate_capcut_project(
                project, expected_asset_count=2
            )

        self.assertTrue(result["passed"])

    def test_committed_capcut_project_validation_rejects_video_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            cuts = project / "shortly_cuts"
            cuts.mkdir()
            asset = cuts / "cut_01.mp4"
            asset.write_bytes(b"fixture")
            document = {
                "duration": 1_000_000,
                "canvas_config": {"ratio": "9:16"},
                "materials": {
                    "videos": [
                        {
                            "id": "material",
                            "type": "video",
                            "path": str(asset),
                            "width": 720,
                            "height": 1280,
                        }
                    ]
                },
                "tracks": [
                    {
                        "type": "video",
                        "segments": [
                            {"material_id": "material", "volume": 1.0}
                        ],
                    }
                ],
            }
            (project / "draft_content.json").write_text(
                __import__("json").dumps(document),
                encoding="utf-8",
            )

            result = validate_capcut_project(project)

        self.assertFalse(result["passed"])
        self.assertIn("audio is not muted", result["errors"][0])


if __name__ == "__main__":
    unittest.main()

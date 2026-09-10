"""Unit tests for app.services.comfy (ComfyUI workflow helpers).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): inject_params / validate_workflow_anchors are pure
functions over a workflow dict. If the module can't be imported in this
environment (missing settings/secret deps), the whole suite skips cleanly.
"""
import copy
import unittest

try:
    from app.services.comfy import (
        BASE_WORKFLOW,
        inject_params,
        validate_workflow_anchors,
        _find_node,
    )
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    BASE_WORKFLOW = None
    inject_params = None
    validate_workflow_anchors = None
    _find_node = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class ValidateAnchorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if validate_workflow_anchors is None:
            raise unittest.SkipTest(
                f"app.services.comfy import failed in this env: {_IMPORT_ERROR}"
            )

    def test_base_workflow_passes(self):
        """The stock workflow has exactly one of every critical anchor."""
        validate_workflow_anchors(copy.deepcopy(BASE_WORKFLOW), None)

    def test_ambiguous_samplers_raise_without_param_map(self):
        """KSampler + KSamplerAdvanced together are ambiguous: no param_map
        means the uploader hasn't said which sampler gets the params."""
        graph = copy.deepcopy(BASE_WORKFLOW)
        graph["3b"] = {
            "class_type": "KSamplerAdvanced",
            "inputs": {"steps": 20, "cfg": 7.0, "noise_seed": 1},
        }
        with self.assertRaises(ValueError) as ctx:
            validate_workflow_anchors(graph, None)
        message = str(ctx.exception)
        self.assertIn("KSampler", message)
        self.assertIn("3", message)
        self.assertIn("param_map", message)

    def test_param_map_targeting_ambiguous_nodes_passes(self):
        """Explicit param_map entries resolve the ambiguity."""
        graph = copy.deepcopy(BASE_WORKFLOW)
        graph["3b"] = {
            "class_type": "KSamplerAdvanced",
            "inputs": {"steps": 20, "cfg": 7.0, "noise_seed": 1},
        }
        graph["20"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        }
        param_map = {
            "steps": ["3", "steps"],
            "batch_size": ["18", "batch_size"],
        }
        validate_workflow_anchors(graph, param_map)

    def test_multiple_latent_images_raise_without_param_map(self):
        graph = copy.deepcopy(BASE_WORKFLOW)
        graph["20"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        }
        with self.assertRaises(ValueError) as ctx:
            validate_workflow_anchors(graph, None)
        self.assertIn("LatentImage", str(ctx.exception))
        self.assertIn("18", str(ctx.exception))
        self.assertIn("20", str(ctx.exception))


class InjectParamsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if inject_params is None:
            raise unittest.SkipTest(
                f"app.services.comfy import failed in this env: {_IMPORT_ERROR}"
            )

    def _inject(self, graph, **overrides):
        args = dict(
            prompt="a cat",
            negative_prompt="blurry",
            steps=8,
            cfg=1.0,
            seed=42,
            aspect_ratio="1:1 (Square)",
            batch_size=7,
        )
        args.update(overrides)
        return inject_params(graph, **args)

    def test_ambiguous_latent_images_leave_batch_size_unset(self):
        """Two LatentImage-ish nodes and no param_map: inject_params must not
        guess which one carries batch_size (R5)."""
        graph = copy.deepcopy(BASE_WORKFLOW)
        graph["20"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        }

        result = self._inject(graph)

        # neither latent node received the injected batch_size=7
        self.assertEqual(result["18"]["inputs"]["batch_size"], 1)
        self.assertEqual(result["20"]["inputs"]["batch_size"], 1)

    def test_ambiguous_samplers_leave_steps_unset(self):
        """KSampler + KSamplerAdvanced and no param_map: the exact-KSampler
        match is unique so steps still inject; the point is the advanced node
        is never touched (it isn't a KSampler)."""
        graph = copy.deepcopy(BASE_WORKFLOW)
        graph["3b"] = {
            "class_type": "KSamplerAdvanced",
            "inputs": {"steps": 20, "cfg": 7.0, "noise_seed": 1},
        }

        result = self._inject(graph)

        self.assertEqual(result["3"]["inputs"]["steps"], 8)
        self.assertEqual(result["3b"]["inputs"]["steps"], 20, "advanced sampler is untouched")

    def test_param_map_override_beats_auto_detect(self):
        """param_map has priority over auto-detect (existing contract)."""
        graph = copy.deepcopy(BASE_WORKFLOW)
        result = self._inject(graph, param_map={"batch_size": ["18", "batch_size"]})
        self.assertEqual(result["18"]["inputs"]["batch_size"], 7)


if __name__ == "__main__":
    unittest.main()

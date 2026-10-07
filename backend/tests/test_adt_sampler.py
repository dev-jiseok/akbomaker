"""Small deterministic logits tests, executable in the optional torch runtime.

Run there with: python -m unittest backend.tests.test_adt_sampler
These verify decoder mechanics, not a pretrained model's accuracy.
"""
import importlib.util
from types import SimpleNamespace
import unittest

from backend.adt_decoding import constrained_sample


@unittest.skipUnless(importlib.util.find_spec("torch"), "Optional torch runtime")
class BudgetTerminationTest(unittest.TestCase):
    def test_exhausted_budget_marks_every_forced_eos_but_not_natural_eos(self):
        import torch
        for final_token in (0, 1, 2, 4, 336, 500, 3):
            with self.subTest(final_token=final_token):
                preferences = [4, 336, 500, final_token]

                def decoder(generated, memory, mask, **kwargs):
                    logits = torch.full((1, generated.shape[1], 528), -10.)
                    logits[0, -1, preferences[generated.shape[1] - 1]] = 10.
                    return logits

                model = SimpleNamespace(config=SimpleNamespace(plain=True, input_sec=2.56),
                                        eval=lambda: None, encoder=lambda x: x,
                                        project_to_mel=lambda x: x, compute_spectrogram=lambda x: x,
                                        decoder=decoder)
                output = constrained_sample(model, torch.zeros(1, 32), None, None, max_length=5)
                self.assertEqual(output.tolist(), [[2, 4, 336, 500, 3]])
                self.assertEqual(model._akbo_decoding_stats["budget_limited"], final_token != 3)


if __name__ == "__main__":
    unittest.main()

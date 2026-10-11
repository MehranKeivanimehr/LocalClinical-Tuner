# Results: base vs fine-tuned

Test set: **300 examples** from 100 held-out encounters (sources: aci-bench, mts-dialog, synthetic). Greedy decoding with identical settings for every model ({'temperature': 0.0, 'max_tokens': 1536, 'repetition_penalty': 1.05}). Values are means with bootstrap 95% CI over test examples (1000 resamples) in parentheses.

## Main comparison

| Metric | Base (Qwen2.5-7B-Instruct) | Base + 2-shot prompting | Base + RAG (2 retrieved examples) | SFT | SFT + ORPO | SFT + ORPO (Q4_K_M GGUF) |
|---|---|---|---|---|---|---|
| Summary ROUGE-L ↑ | 0.383 (0.368-0.399) | 0.427 (0.409-0.445) | 0.454 (0.435-0.471) | **0.558 (0.535-0.580)** | 0.548 (0.522-0.573) | 0.544 (0.519-0.568) |
| Summary BERTScore F1 ↑ | 0.411 (0.392-0.430) | 0.454 (0.433-0.475) | 0.482 (0.460-0.503) | **0.569 (0.547-0.590)** | 0.567 (0.545-0.588) | 0.559 (0.535-0.582) |
| Summary key-entity coverage ↑ | 67.0% (61.4-72.8) | 75.3% (70.4-80.2) | 75.5% (71.0-80.6) | 77.2% (72.6-81.5) | 75.1% (70.5-79.7) | **79.5% (75.1-83.5)** |
| Summary length ≤ 132 words (120 + 10%) ↑ | 98.0% (95.0-100.0) | 89.0% (82.0-95.0) | 80.0% (72.0-87.0) | **99.0% (97.0-100.0)** | **99.0% (97.0-100.0)** | 98.0% (95.0-100.0) |
| Summary medication hallucination ↓ | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** |
| SOAP ROUGE-L ↑ | 0.583 (0.553-0.611) | 0.635 (0.604-0.663) | 0.677 (0.642-0.707) | 0.749 (0.718-0.776) | **0.752 (0.721-0.778)** | 0.747 (0.716-0.772) |
| SOAP BERTScore F1 ↑ | 0.471 (0.454-0.489) | 0.575 (0.551-0.600) | 0.660 (0.633-0.685) | 0.736 (0.701-0.765) | **0.748 (0.727-0.769)** | 0.737 (0.716-0.758) |
| SOAP structure adherence ↑ | 99.2% (97.8-100.0) | 99.8% (99.2-100.0) | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** |
| SOAP key-entity coverage ↑ | 94.6% (91.8-97.0) | 95.3% (92.8-97.5) | 96.1% (93.7-98.0) | 95.4% (92.7-97.9) | 96.3% (93.6-98.4) | **97.1% (95.1-98.9)** |
| SOAP medication hallucination ↓ | **0.0% (0.0-0.0)** | 3.0% (0.0-7.0) | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** | **0.0% (0.0-0.0)** |
| Entity F1 (relaxed) ↑ | 0.630 (0.600-0.659) | 0.677 (0.651-0.703) | 0.738 (0.711-0.763) | **0.829 (0.803-0.849)** | 0.829 (0.803-0.849) | 0.816 (0.790-0.838) |
| Entity F1 (strict) ↑ | 0.544 (0.507-0.579) | 0.610 (0.577-0.640) | 0.679 (0.645-0.708) | **0.798 (0.771-0.819)** | 0.796 (0.769-0.819) | 0.774 (0.744-0.800) |
| Symptom negation accuracy ↑ | 97.6% | 98.6% | 99.6% | **100.0%** | **100.0%** | 99.9% |
| Valid JSON ↑ | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** | **100.0% (100.0-100.0)** |

## LLM-as-a-judge (judge: `Qwen/Qwen2.5-72B-Instruct`, reference-free, 1-5)

| Dimension | Base (Qwen2.5-7B-Instruct) | SFT | SFT + ORPO |
|---|---|---|---|
| faithfulness | 4.480 (4.360-4.593) | 4.720 (4.627-4.807) | 4.500 (4.370-4.637) |
| completeness | 4.307 (4.180-4.433) | 4.493 (4.360-4.620) | 4.308 (4.164-4.452) |
| format | 4.927 (4.873-4.973) | 4.933 (4.860-4.987) | 4.829 (4.733-4.918) |
| clinical_usefulness | 4.487 (4.380-4.587) | 4.607 (4.473-4.720) | 4.418 (4.274-4.569) |
| overall | 4.373 (4.267-4.473) | 4.573 (4.467-4.673) | 4.384 (4.260-4.507) |

**Pairwise (each comparison judged in both presentation orders; disagreement counts as a tie)**

| Comparison | n | win | tie | loss | position consistency |
|---|---|---|---|---|---|
| SFT + ORPO vs Base (Qwen2.5-7B-Instruct) | 150 | 20.7% | 45.3% | 34.0% | 56.0% |
| SFT + ORPO vs SFT | 150 | 4.7% | 74.0% | 21.3% | 50.0% |
| SFT vs Base (Qwen2.5-7B-Instruct) | 150 | 25.3% | 41.3% | 33.3% | 60.0% |

Pairwise by task (win / tie / loss, %):

| Comparison | summary | soap | entities |
|---|---|---|---|
| SFT + ORPO vs Base (Qwen2.5-7B-Instruct) | 22 / 54 / 24 | 12 / 48 / 40 | 28 / 34 / 38 |
| SFT + ORPO vs SFT | 8 / 74 / 18 | 2 / 76 / 22 | 4 / 72 / 24 |
| SFT vs Base (Qwen2.5-7B-Instruct) | 30 / 50 / 20 | 10 / 40 / 50 | 36 / 34 / 30 |

## By data source

**aci-bench**

| Metric | Base (Qwen2.5-7B-Instruct) | Base + 2-shot prompting | Base + RAG (2 retrieved examples) | SFT | SFT + ORPO | SFT + ORPO (Q4_K_M GGUF) |
|---|---|---|---|---|---|---|
| Summary ROUGE-L | 0.433 | 0.461 | 0.475 | 0.468 | 0.462 | 0.455 |
| Summary key-entity coverage | 63.4% | 80.4% | 60.7% | 64.3% | 58.0% | 67.9% |
| Summary length ≤ 132 words (120 + 10%) | 100.0% | 100.0% | 75.0% | 100.0% | 100.0% | 100.0% |
| Summary medication hallucination | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| SOAP ROUGE-L | 0.409 | 0.406 | 0.467 | 0.498 | 0.531 | 0.522 |
| SOAP structure adherence | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| SOAP key-entity coverage | 92.9% | 90.2% | 90.2% | 86.6% | 90.2% | 92.9% |
| SOAP medication hallucination | 0.0% | 25.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Entity F1 (relaxed) | 0.410 | 0.512 | 0.400 | 0.607 | 0.545 | 0.619 |
| Entity F1 (strict) | 0.337 | 0.442 | 0.232 | 0.584 | 0.523 | 0.524 |
| Symptom negation accuracy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Valid JSON | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

**mts-dialog**

| Metric | Base (Qwen2.5-7B-Instruct) | Base + 2-shot prompting | Base + RAG (2 retrieved examples) | SFT | SFT + ORPO | SFT + ORPO (Q4_K_M GGUF) |
|---|---|---|---|---|---|---|
| Summary ROUGE-L | 0.409 | 0.468 | 0.475 | 0.515 | 0.500 | 0.514 |
| Summary key-entity coverage | 88.6% | 87.1% | 84.6% | 92.1% | 89.3% | 94.8% |
| Summary length ≤ 132 words (120 + 10%) | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Summary medication hallucination | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| SOAP ROUGE-L | 0.379 | 0.474 | 0.467 | 0.605 | 0.586 | 0.583 |
| SOAP structure adherence | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| SOAP key-entity coverage | 93.2% | 93.5% | 95.9% | 92.8% | 91.3% | 93.8% |
| SOAP medication hallucination | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Entity F1 (relaxed) | 0.518 | 0.586 | 0.613 | 0.675 | 0.672 | 0.641 |
| Entity F1 (strict) | 0.419 | 0.487 | 0.457 | 0.580 | 0.585 | 0.536 |
| Symptom negation accuracy | 97.1% | 96.2% | 100.0% | 100.0% | 100.0% | 100.0% |
| Valid JSON | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

**synthetic**

| Metric | Base (Qwen2.5-7B-Instruct) | Base + 2-shot prompting | Base + RAG (2 retrieved examples) | SFT | SFT + ORPO | SFT + ORPO (Q4_K_M GGUF) |
|---|---|---|---|---|---|---|
| Summary ROUGE-L | 0.370 | 0.410 | 0.444 | 0.579 | 0.570 | 0.561 |
| Summary key-entity coverage | 59.2% | 70.7% | 73.0% | 72.4% | 70.8% | 74.4% |
| Summary length ≤ 132 words (120 + 10%) | 97.1% | 84.3% | 72.9% | 98.6% | 98.6% | 97.1% |
| Summary medication hallucination | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| SOAP ROUGE-L | 0.669 | 0.707 | 0.766 | 0.817 | 0.826 | 0.820 |
| SOAP structure adherence | 98.9% | 99.6% | 100.0% | 100.0% | 100.0% | 100.0% |
| SOAP key-entity coverage | 95.2% | 96.3% | 96.5% | 96.9% | 98.5% | 98.6% |
| SOAP medication hallucination | 0.0% | 2.9% | 0.0% | 0.0% | 0.0% | 0.0% |
| Entity F1 (relaxed) | 0.651 | 0.695 | 0.764 | 0.855 | 0.857 | 0.843 |
| Entity F1 (strict) | 0.566 | 0.632 | 0.721 | 0.831 | 0.830 | 0.810 |
| Symptom negation accuracy | 97.6% | 99.0% | 99.5% | 100.0% | 100.0% | 99.8% |
| Valid JSON | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |


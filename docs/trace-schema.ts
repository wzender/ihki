/**
 * The scorer's result for one recording, with the teacher's diagnostic trace: the data of the trace dashboard
 * (static/trace.html). Produced by Scorer.score(..., diagnostics=True) in app/scorer.py; POST /api/score returns it
 * to a teacher, and POST /api/lab/score to the lab. A mock from the real pipeline: scripts/mock_trace.py writes
 * static/assets/trace-mock.json.
 *
 * Units: scores are 0..100; probabilities 0..1 (posteriors of the wav2vec2 CTC model); `logp` and `gop` are natural
 * logs; frames are the model's (~20 ms each, `TraceFrames.ms`), [start, end) half-open.
 * IPA symbols are the phrase bank's. `sound` is the archi.py symbol of a letter with several correct realisations
 * (Q for ق: ʔ / k / q; K, TH, DH, ZH, J, E, O), and equals `ipa` for any other sound.
 * This file is documentation: nothing compiles it.
 */

export type Ipa = string;
/** "_" is "nothing heard" (a deletion). */
export type Heard = Ipa | "_";

/** The whole result of one recording. */
export interface ScoreResult {
  score: number;                  // the sentence's score (TraceSentence.final)
  variant: { ar: string; tr: string; ipa: string };  // the accepted reading the audio fit best
  heard_ipa: string;              // greedy decode: what the model heard, free of the expected text
  heard_tr: string;               // the same in Hebrew letters, cut at the aligned word starts
  unrelated: boolean;             // a different sentence (TraceUnrelated): score capped, no tips
  uncertain: boolean;             // marginal target fit: score retained, categorical sound feedback suppressed
  uncertainty_message?: string;
  words: Word[];
  tips: Tip[];                    // the most crucial mistakes, severity first (at most config.max_tips)
  issues: Issue[];                // the rest of the imprecise sounds
  references: number;             // native TTS baselines used
  dialect?: "urban" | "rural";    // what the accepted realisations point to (never part of the score)
  mixed_dialect?: boolean;
  calibrated?: { expected: Ipa; heard: Heard; from: number; to: number }[];   // teacher floors that applied
  phoneme_heatmap?: Heatmap;
  trace?: Trace;                  // teachers only
  phrase?: { he: string; ar: string };   // the mock only: what was said
}

export interface Word {
  ar: string;
  tr: string;                     // Hebrew transliteration of the expected word
  heard_tr: string;
  score: number;                  // word_min_weight · min + (1 − word_min_weight) · mean of its phonemes
  letters: { t: string; score: number; why?: string; tip?: string }[];   // the transliteration letter by letter
  phonemes: Phoneme[];
}

export interface Phoneme {
  ipa: Ipa;
  word: number;
  score: number;
  flagged: boolean;               // under flag_below and beyond the pair's margin: a mistake
  heard: Heard | null;            // set when flagged
  sound?: string;                 // archi symbol, when not ipa
  realised?: Ipa;                 // a class sound: the realisation the model heard (ʔ for Q)
  inserted?: Ipa;                 // a vowel heard before this consonant (rule 3.10)
}

export interface Tip { title: string; text: string; key: string; severity: 1 | 2 | 3; word: string; word_tr: string; score: number }
export interface Issue { sound: string; word_tr: string; score: number; missing: boolean; heard: string | null }

export interface Heatmap {
  duration_ms: number | null;
  frames: number;
  frame_bins: number;             // ≤ 180: each bin is the max over its frames
  rows: { phoneme: string; expected: boolean; values: number[] }[];   // values 0..100, one per bin
  tracks: { expected: Span[]; heard: Span[] };
}
export interface Span { phoneme: Ipa; start: number; end: number }

// ---------------------------------------------------------------- the trace

export interface Trace {
  config: TraceConfig;
  /** Stage 0, Viterbi: every accepted reading (and its diphthong forms) the audio was aligned to. */
  variants: TraceVariant[];
  frames: TraceFrames;
  aipa: string;                   // the chosen reading with archi symbols
  unrelated: TraceUnrelated;
  references: { mean: number; sounds: number }[];
  /** Stages 1–5 per sound, in order: gop → exemption → insertion → baseline → calibration → final. */
  tokens: TraceToken[];
  words: TraceWord[];
  sentence: TraceSentence;
  tips: { key: string; severity: 1 | 2 | 3; score: number }[];
}

export interface TraceConfig {
  leniency: number;               // gop_factor = exp(gop · leniency)
  confident_peak: number;         // confidence = min(1, p_target / confident_peak)
  consonant_window: number | null; // max context frames on either side of a consonant span
  deleted_peak: number;           // deleted: p_target < deleted_peak and p_competitor < deleted_comp
  deleted_comp: number;
  flag_below: number;             // a sound under this (and beyond its margin) is flagged
  issue_below: number;            // a sound under this is listed as imprecise
  near_vowel_floor: number;
  word_min_weight: number;
  vowel_weight: number;           // a vowel's weight in the sentence's phone mean
  unrelated_ratio: number;
  feedback_uncertain_ratio: number;
  unrelated_cap: number;
  letter_good: number;
  max_tips: number;
  max_issues: number;
  ref_min_mean: number;
  margin_default: number;
  ayin_deleted_floor: number;
  margins: { target: Ipa; heard: Heard; margin: number }[];   // the pairs that need a clearer win (rules 6.2)
  exemptions: ("glottal_dropped" | "article_vowel_elided" | "near_vowel" | "unmarked_short_vowel" | "final_b_devoicing" | "word_final_vowel_linking")[];
  insertion: { score_cap: number; min_peak: number; min_frames: number } | null;
  disabled: string[];             // rule switches the teacher turned off
}

export interface TraceVariant {
  ar: string;
  ipa: string;
  logp: number | null;            // the CTC path's log-prob (null: the audio is too short for this reading)
  chosen: boolean;                // the highest logp wins
  tokens: string[];               // the path's tokens (archi symbols), in order
}

export interface TraceFrames {
  count: number;                  // T
  ms: number;                     // per frame
  energy: number[] | null;        // RMS per frame, 0..1 of the loudest (null when scored without the audio)
}

export interface TraceUnrelated {
  heard: string;                  // greedy decode
  distance: number;               // edit distance from the chosen reading (class members count as equal)
  tokens: number;
  ratio: number;                  // distance / tokens
  threshold: number;              // over this: a different sentence
  uncertain_threshold: number;    // at/over this: suppress categorical phoneme feedback
  uncertain: boolean;
  unrelated: boolean;
}

export type StageName = "gop" | "exemption" | "insertion" | "baseline" | "calibration";
export type Stage =
  | { stage: "gop"; score: number }
  | { stage: "exemption"; score: number; applied: TraceConfig["exemptions"][number] | null }
  | { stage: "insertion"; score: number; applied: Ipa | null }   // the vowel heard inside the cluster
  | { stage: "baseline"; score: number; ref: number | null; applied: boolean }   // ref: the native voice's score
  | { stage: "calibration"; score: number; floor: number | null; applied: boolean };

export interface TraceToken {
  i: number;
  word: number;
  ipa: Ipa;
  sound: string;
  class: Ipa[] | null;            // a class sound's accepted realisations
  realised: Ipa | null;
  span: [number, number];         // frames the CTC path emits it in
  window: [number, number];       // frames it was read from: the end of the sound before → the start of the one after
  curves: TraceCurves;
  p_target: number;               // peak posterior of the sound in its window
  competitor: Ipa | null;         // strongest other sound, neighbours and blank excluded (null: none, or unnamed)
  competitor_other: boolean;      // the strongest is a sound outside Arabic and Hebrew
  p_competitor: number;           // its peak posterior in the window
  competitor_frames: number;      // frames where it clears deleted_comp; one frame may be an onset transient
  gop: number;                    // ln(p_target / max(p_target, p_competitor)) ≤ 0
  confidence: number;
  gop_factor: number;
  confidence_factor: number;      // score = 100 · gop_factor · confidence_factor
  deleted: boolean;
  stages: Stage[];
  final: number;
  margin: { needed: number; got: number; beyond: boolean };   // the competitor's win against the pair's margin
  vowel: boolean;
  weight: number;
  flagged: boolean;
  rule: { id: string; title: string; severity: 1 | 2 | 3 } | null;   // pronunciation-rules.md
  tip: boolean;
  issue: boolean;
}

/** Posteriors frame by frame around a sound: frames curves.start .. start + length, its window ± 4. */
export interface TraceCurves {
  start: number;
  target: number[];
  competitor: number[] | null;
  prev: number[] | null;          // the sound before (masked: never a competitor)
  next: number[] | null;          // the sound after (masked too)
  blank: number[];
}

export interface TraceWord { tr: string; scores: number[]; min: number; mean: number; score: number }

export interface TraceSentence {
  phone_mean: number;             // weighted: vowels count vowel_weight
  word_mean: number;
  phone_weight: number;
  word_weight: number;
  before_cap: number;             // phone_weight · phone_mean + word_weight · word_mean
  final: number;
  capped: boolean;                // unrelated: capped at unrelated_cap
}

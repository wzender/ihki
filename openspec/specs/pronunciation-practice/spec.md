# Pronunciation Practice

## Purpose

Define how learners receive prompts, record spoken Arabic, and receive phoneme-level feedback.

## Requirements

### Requirement: Available phrase bank
The service SHALL return only the phrases and lesson content available to the current learner, including teacher-added texts in the learner's classes.

#### Scenario: Learner requests practice content
- **GIVEN** the learner is authorized for practice
- **WHEN** the learner requests the phrase bank
- **THEN** the response contains the built-in phrases and any eligible class text lines
- **AND** content outside the learner's lessons is excluded

### Requirement: Pronunciation scoring
The service SHALL compare a recording with every accepted pronunciation variant and return a 0–100 result with word- and phoneme-level evidence.

#### Scenario: Recording matches an accepted variant
- **WHEN** a learner submits decodable audio for an available phrase
- **THEN** the service selects the best-fitting accepted variant
- **AND** returns sentence, word, and phoneme scores
- **AND** identifies substitutions and available Hebrew articulation tips

#### Scenario: Recording is unrelated
- **WHEN** the free phoneme decode is too far from every accepted variant
- **THEN** the result identifies the recording as an unrelated sentence
- **AND** does not present individual pronunciation tips as reliable findings

#### Scenario: Audio cannot be scored
- **WHEN** uploaded audio is empty, oversized, unsupported, or undecodable
- **THEN** the service rejects the attempt without recording a scored result

### Requirement: Native reference compensation
When a native TTS reference is available, the service SHALL compare learner sounds with the same model's scores for that reference so model errors in native speech do not unfairly reduce the learner score.

#### Scenario: Model also mishears the reference
- **GIVEN** the target sound is weak or misrecognized in the native reference
- **WHEN** the learner's result is assembled
- **THEN** the learner is not penalized solely for that model limitation
- **AND** no substitution tip is shown for it

### Requirement: Accessible feedback
The browser SHALL expose low-scoring letter feedback without requiring a mouse.

#### Scenario: Learner taps a marked letter
- **GIVEN** a letter has a score below 75 and a reason
- **WHEN** the learner taps the dotted-underlined letter
- **THEN** the browser shows the reason and tip near that letter

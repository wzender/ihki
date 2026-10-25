## MODIFIED Requirements

### Requirement: Native reference compensation
When a native TTS reference is available, the service SHALL compare learner sounds with the same model's scores for that reference so model errors in native speech do not unfairly reduce the learner score. A reference SHALL compensate a learner's substitution only when the reference lost that sound too, and never for a protected substitution pair.

#### Scenario: Model also mishears the reference
- **GIVEN** the target sound is weak or misrecognized in the native reference
- **AND** the learner's realization of it is weak or misrecognized too
- **WHEN** the learner's result is assembled
- **THEN** the learner is not penalized solely for that model limitation
- **AND** no substitution tip is shown for it

#### Scenario: Reference lost the sound differently
- **GIVEN** the model heard ح in the native reference as خ
- **AND** heard ح in the learner's recording as ه, a pair that is not protected
- **WHEN** the learner's result is assembled
- **THEN** the reference compensates the learner's score for ح, as it shows the model loses ح in this phrase

#### Scenario: Reference kept the sound
- **GIVEN** the model recognized the target sound in the native reference
- **AND** heard another sound in its place in the learner's recording
- **WHEN** the learner's result is assembled
- **THEN** the learner's score for that sound is not raised by that reference

#### Scenario: Protected substitution pair
- **GIVEN** a substitution pair listed as protected in the scoring rules, such as ح heard as خ
- **AND** every native reference lost ح the same way
- **WHEN** a learner's ح is heard as خ
- **THEN** no reference raises the learner's score for that sound
- **AND** the substitution tip is shown when the score is below the tip threshold

#### Scenario: No native reference
- **GIVEN** no native reference can be built for the phrase (TTS unavailable or the phrase not recognized in it)
- **WHEN** the learner's result is assembled
- **THEN** scoring proceeds without compensation, as before

## ADDED Requirements

### Requirement: Evidence before a substitution tip
The service SHALL give a substitution tip, and a letter's "heard as" reason, only when the competing sound has acoustic evidence: it wins at least one frame in the target's window, or reaches the confident posterior. Without that evidence, the sound keeps its score and gets no substitution tip.

#### Scenario: Competitor never wins a frame
- **GIVEN** the target ء at the start of احنا has a posterior of 0.06
- **AND** its strongest competitor ب has 0.08 and wins no frame in the window
- **WHEN** the result is assembled
- **THEN** no tip saying ء was heard as ب is shown
- **AND** the letter shows no "heard as" reason

#### Scenario: Competitor wins frames
- **GIVEN** the model heard خ in place of ح, winning at least one frame in its window
- **WHEN** the result is assembled with a score below the tip threshold
- **THEN** the substitution tip and the letter's "heard as" reason are shown

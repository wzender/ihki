# Spec Delta

## Purpose

Lets the course's own staff author the Arabic workbook (levels, lessons, topics, items and the words they use) as
versioned data, and publish a revision to students without a deploy and without detaching their progress.

## ADDED Requirements

### Requirement: Versioned course document
The service SHALL keep the Arabic course as versions of one document (levels, lessons, topics, items), each a draft, the published version, or history, and SHALL serve exactly one published version to learners.

#### Scenario: Learners get the published version
- **GIVEN** version 4 is published and version 5 is a draft
- **WHEN** a learner loads the lessons and their sentences
- **THEN** every lesson, topic and sentence comes from version 4
- **AND** nothing of version 5 is visible to the learner

#### Scenario: First use imports the built-in workbook
- **GIVEN** Supabase is configured and no course version exists
- **WHEN** an authorized manager opens the course editor or the course tables are first read
- **THEN** the built-in workbook is stored as version 1, published, with the same lessons, topics, order and sentences learners see today

#### Scenario: No Supabase
- **WHEN** Supabase is not configured
- **THEN** learners get the built-in workbook exactly as before this change
- **AND** the course editor and the course MCP tools report that authoring needs Supabase

### Requirement: Stable item keys keep progress
Every course item SHALL have a key fixed when it is created, and the service SHALL record and read a learner's attempts on an item by its topic and key, so that editing an item's text, moving it within its topic, or republishing keeps its progress.

#### Scenario: Imported items keep today's progress
- **GIVEN** a learner practised a built-in sentence before the import
- **WHEN** the imported version 1 is published
- **THEN** the learner's best score and attempts on that sentence are unchanged

#### Scenario: A reworded sentence keeps its progress
- **GIVEN** a learner has a gold bead on an item
- **WHEN** a teacher corrects the item's Hebrew or Arabic and the version is published
- **THEN** the learner still has the gold bead on that item

#### Scenario: A deleted item retires its progress
- **WHEN** a published version no longer has an item
- **THEN** its attempts stay stored but no longer count toward the lesson
- **AND** publishing the earlier version again counts them again

### Requirement: Drafting course changes
An authorized editor SHALL be able to change a draft: add, edit, delete and reorder items within a topic; add, rename, delete and reorder topics within a lesson; and add lessons to a level. Each change SHALL apply to the editor's current draft, created from the published version when none exists.

#### Scenario: Editing starts a draft
- **GIVEN** version 4 is published and there is no draft
- **WHEN** an editor saves a change to a topic
- **THEN** version 5 is created as a copy of version 4 with that change, as a draft
- **AND** learners still get version 4

#### Scenario: Topic kinds keep their rules
- **WHEN** an editor adds an item to a "match" topic or a "write" topic, which have no items of their own
- **THEN** the change is rejected with a message naming the topic's kind
- **AND** a "learn" topic accepts word items and a "say" or "read" topic accepts sentence items

#### Scenario: Two editors at once
- **WHEN** an editor saves a change prepared against a draft that another editor changed since
- **THEN** the change is refused and asks to be prepared again

### Requirement: Items are compiled and checked
Every item SHALL be compiled into its Arabic spelling, transliteration and accepted pronunciations when it is saved, from Arabic script or from Arabic in Hebrew letters, and the save SHALL report every word read by spelling rules rather than found in the course dictionary.

#### Scenario: A known word
- **WHEN** an item contains only words of the course dictionary
- **THEN** it is saved with the dictionary's spelling and pronunciations and no words are reported

#### Scenario: An unknown word is read by rule
- **WHEN** an item, written in Hebrew letters with points, contains a word the dictionary lacks
- **THEN** the item is saved with that word read by rule
- **AND** the response lists the word for review

#### Scenario: A word that cannot be read
- **WHEN** an item contains a word in Arabic script that the dictionary lacks, or text that is neither Arabic nor Hebrew letters
- **THEN** the item is not saved and the response names the word and how to write it

### Requirement: Review before publishing
Before a draft is published, an authorized reviewer SHALL be able to see its diff against the published version and preview it as a learner, and the diff SHALL count, for each changed or deleted item, the learners who practised it, without identifying them.

#### Scenario: The diff
- **WHEN** a reviewer opens a draft's diff
- **THEN** it lists added, changed, moved and deleted lessons, topics and items, and the words read by rule
- **AND** each deleted item shows how many learners have attempts on it

#### Scenario: Preview as a learner
- **WHEN** a reviewer turns on preview of the draft
- **THEN** their own app shows the draft's path, topics and sentences, and practice on them is scored
- **AND** other users still get the published version

### Requirement: Publishing and rolling back
An authorized publisher SHALL be able to publish a draft or any earlier version, which SHALL take effect for every learner within one minute without a deploy, and the previously published version SHALL become history.

#### Scenario: Publish
- **WHEN** a publisher publishes version 5
- **THEN** within a minute every learner's next load shows version 5
- **AND** version 4 becomes history and the event is logged with the publisher and the time

#### Scenario: Roll back
- **WHEN** a publisher publishes history version 4 again
- **THEN** learners get version 4 within a minute
- **AND** progress on items present in version 4 counts as before

#### Scenario: A class's lessons and homework point at removed content
- **WHEN** a version without a lesson or topic is about to be published while a class has that lesson or a homework on that topic
- **THEN** the publisher is shown the affected classes before confirming
- **AND** after publishing, those classes no longer show the missing lesson or homework, and nothing else of the class changes

### Requirement: Course dictionary
An authorized editor SHALL be able to add a word to the course dictionary with its Arabic, its transliteration in Hebrew letters and its meaning, and its pronunciations SHALL be derived by rule until a developer refines them.

#### Scenario: Adding a word
- **WHEN** an editor adds a word that the dictionary lacks
- **THEN** the word is stored and items that use it are no longer reported as read by rule
- **AND** its derived pronunciations are shown to developers for refinement

#### Scenario: Pronunciations are developers' only
- **WHEN** an editor who is not a developer tries to change a word's accepted pronunciations
- **THEN** the change is refused

### Requirement: Authoring permissions
Drafting and adding words SHALL require the course-editing permission and publishing SHALL require the course-publishing permission; both SHALL be given to managers of the Arabic subject and to developers, and learners and class teachers SHALL only read the published course.

#### Scenario: A class teacher
- **WHEN** a class teacher who is not a manager tries to change a draft or publish
- **THEN** the request is refused
- **AND** nothing is stored

#### Scenario: A manager of another subject
- **WHEN** a manager of the mathematics subject tries to change the Arabic course
- **THEN** the request is refused

### Requirement: Authoring through the MCP
The MCP SHALL let an authorized manager read the course outline and a topic's items, prepare a change to the draft, publish the prepared change, and publish a version, with the same single-use, manager-bound, ten-minute approvals as other changes.

#### Scenario: Prepare, then confirm
- **WHEN** Grok prepares a change that adds three sentences to a topic
- **THEN** the response shows the compiled items, the words read by rule and the draft they will join, and changes nothing
- **AND** only a publish call with the returned approval, made after the manager confirms, saves them to the draft

#### Scenario: A whole topic from a workbook page
- **WHEN** Grok prepares a replacement of a topic's items read from a revised workbook page
- **THEN** the preview pairs each new item with the item it replaces, keeps the key of every item it matches, and lists items that would be deleted with their learner counts

#### Scenario: Publishing a version through the MCP
- **WHEN** Grok is asked to publish a draft
- **THEN** it first returns the diff summary and the affected classes as a prepared action
- **AND** the version is published only after the manager confirms

### Requirement: Degraded operation
The service SHALL keep serving the last published version it loaded when Supabase is unavailable, and SHALL fall back to the built-in workbook only when it has never loaded a version.

#### Scenario: Supabase is down at startup
- **GIVEN** the service published and cached version 5 earlier
- **WHEN** the service starts while Supabase cannot be reached
- **THEN** learners get version 5 from the cache
- **AND** the editor shows that authoring is unavailable until Supabase answers

#### Scenario: A version that does not compile
- **WHEN** a stored version fails to compile on load
- **THEN** the service keeps serving the version it had and logs the failure
- **AND** that version cannot be published until it compiles

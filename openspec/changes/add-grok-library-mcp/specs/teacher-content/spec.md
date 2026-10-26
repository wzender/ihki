# Spec Delta

## ADDED Requirements

### Requirement: External digital-library management
An authorized external integration SHALL be able to list, prepare, publish, and rename digital-library videos under the same title, provider-link, ordering, and visibility rules as authorized staff using the management interface.

#### Scenario: Integration publishes a prepared video
- **WHEN** an authorized integration publishes a valid prepared YouTube or Vimeo video
- **THEN** the video is appended to the digital library and becomes available to learners with library access

#### Scenario: Integration renames a library video
- **WHEN** an authorized integration supplies an existing video identifier and a valid title
- **THEN** subsequent library reads show the new title without changing the video URL or position

#### Scenario: Integration lists the library
- **WHEN** an authorized integration requests the digital library
- **THEN** it receives the identifiers, titles, provider URLs, and current order needed for the permitted tools, but no learner data

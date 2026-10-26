# Spec Delta

## MODIFIED Requirements

### Requirement: Class configuration
Authorized staff SHALL be able to manage a class's students, nicknames, teachers, lessons, subject, homeworks, and scheduled daily videos through the management interface, while an authorized external integration SHALL be limited to class creation, class renaming, and individual student membership changes that preserve all other class settings.

#### Scenario: Class configuration changes
- **WHEN** an authorized staff member saves valid class settings
- **THEN** the settings are available to class members on their next page load
- **AND** duplicate homework topics and invalid subjects are rejected

#### Scenario: External integration changes a roster
- **WHEN** an authorized external integration confirms a valid student addition or removal
- **THEN** the class roster changes under the same email and nickname validation rules as the management interface
- **AND** teachers, lessons, subject, homeworks, and scheduled daily videos remain unchanged

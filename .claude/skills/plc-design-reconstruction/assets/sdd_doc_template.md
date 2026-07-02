# \<Name of Project or Feature\>

## PLC-L1: Basic PLC: Software Architecture and Design Document

## Documentation Control

| Item | Description |
| :---- | :---- |
| Title | \<doc title\> |
| Author(s) | \<author\> |
| Revision | \<date\> |
| State | \<Development, Baselined, Released for Approval, Approved\> |
| Reviewed in | \<tool\> |
| PLC-L1 SADD Template Revision | SWE-PLC-L1-002-BasicPLC-SADD-TMPL, Oct.5, 2023 |

Approvers

| Date | Name | Notes |
| :---- | :---- | :---- |
| \<month dd, yyyy\> | \<Full Name\> | \<Brief description.\> |
|  |  |  |

Reviewers

(NOTE: Please ensure that reviewers include:

* impacted BUs (Business Units) or other NVIDIA product teams, as needed.  
* internal representatives for customers or partners (e.g. ISVs) to ensure that API changes do not impact applications.)

| Date | Name | Notes |
| :---- | :---- | :---- |
| \<month dd, yyyy\> | \<Full Name\> | \<Brief description.\> |
|  |  |  |

  Revision History

| Date | Author | Summary of Change |
| :---- | :---- | :---- |
| \<month dd, yyyy\> | \<Full Name\> | \<Brief description.\> |
|  |  |  |

Table of Contents

[**Introduction**](#introduction)	**[4](#introduction)**

[Purpose and Scope](#purpose-and-scope)	[4](#purpose-and-scope)

[Assumptions](#assumptions)	[4](#assumptions)

[Constraints](#constraints)	[4](#constraints)

[Dependencies](#dependencies)	[4](#dependencies)

[Definitions, Acronyms, Abbreviations](#definitions,-acronyms,-abbreviations)	[4](#definitions,-acronyms,-abbreviations)

[References](#references)	[4](#references)

[**Architectural Details**](#architectural-details)	**[4](#architectural-details)**

[**Design Details**](#design-details)	**[5](#design-details)**

[Design Alternatives](#design-alternatives)	[5](#design-alternatives)

[Static Design](#static-design)	[5](#static-design)

[Configuration Data](#configuration-data)	[5](#configuration-data)

[External Interface and Specification](#external-interface-and-specification)	[5](#external-interface-and-specification)

[Dependencies](#dependencies-1)	[6](#dependencies-1)

[Integration Validation Plan](#integration-validation-plan)	[6](#integration-validation-plan)

[Dynamic Design](#dynamic-design)	[7](#dynamic-design)

[Functionality and Behavior](#functionality-and-behavior)	[7](#functionality-and-behavior)

[Control Flow](#oct.5,-2023)	[7](#oct.5,-2023)

[Data Flow](#data-flow)	[7](#data-flow)

[Error Handling](#error-handling)	[7](#error-handling)

[Logging and Debugging](#logging-and-debugging)	[7](#logging-and-debugging)

[State Machine](#state-machine)	[7](#state-machine)

[Security Design](#security-design)	[7](#security-design)

[Test Automation](#test-automation)	[7](#test-automation)

[Other Design Considerations](#other-design-considerations)	[8](#other-design-considerations)

[Resource Limits](#resource-limits)	[8](#resource-limits)

[High Availability](#high-availability)	[9](#high-availability)

[Scalability](#scalability)	[9](#scalability)

[Future Work](#future-work)	[9](#future-work)

1. # Introduction {#introduction}

   1. ## Purpose and Scope {#purpose-and-scope}

The purpose of this document is to record an architectural details design that realizes the software requirements outlined in \<Name of Project or Feature\> SRD.

2. ## Assumptions {#assumptions}

\<Enter your text here. List the assumptions made in the architecture and design.\>

3. ## Constraints {#constraints}

\<Enter your text here. List the constraints that the project will face during development (for example: platforms to be used, development environment, legacy software needed, etc.)\>

4. ## Dependencies {#dependencies}

\<Optional: enter explanatory text before the table.\>

\<Populate the table below. List the internal and external dependencies that will need to be managed. What team or external company are you dependent on? What do you need them to deliver?\>

| Team or Company | Deliverable |
| :---- | :---- |
|  |  |
|  |  |

5. ## Definitions, Acronyms, Abbreviations {#definitions,-acronyms,-abbreviations}

| Term or Abbreviation | Description |
| :---- | :---- |
|  |  |
|  |  |

   6. ## References {#references}

| SWE Number | Input Work Product | Revision | Location |
| :---- | :---- | :---- | :---- |
| \<SWE number\> | \<input document title\> | \<revision date or \#\> | \<[link](https://www.nvidia.com/en-us/about-nvidia/)\> |
|  |  |  |  |

2. # Architectural Details {#architectural-details}

\<This section is needed for newly architected software element/component/unit, or if the new feature required major changes to the existing design/code base. Once done with this whole section, remove this paragraph or replace it with a short introduction, e.g., “This section documents relevant architectural design options and selects one option for implementation using defined criteria.”.\>

**Provide a high-level description of the architecture and include block diagram(s) if applicable**  
\<Enter your text here.\>

**Describe the static and dynamic aspects of the architecture**  
\<Enter your text here.\>

**Describe key assumptions and limitations (if any) of the architecture**  
\<Enter your text here.\>

3. # Design Details {#design-details}

\<The design shall capture applicable sections 3.1 through 3.6.4. Add any relevant information not listed below. Remove sections that are not applicable to your design. Once done with this whole section, remove this paragraph or replace it with a short introduction, e.g., “This chapter documents relevant software design that satisfies the software requirements of \<Name of Project or Feature\>.”.\>

1. ## Design Alternatives {#design-alternatives}

\<Enter your text here. Describe all relevant design options and selects one option for implementation using defined criteria.\>

2. ## Static Design {#static-design}

   1. ### Configuration Data {#configuration-data}

\<Enter your text here. List the configuration data required for each component or software module. Note that this is part of your attack surface. Elaborate startup parameters for the executable, registry keys, environment variables and other ‘external’ sources of data.\>

2. ### External Interface and Specification {#external-interface-and-specification}

| Describe interfaces with other software, including those of other operational capabilities, internal and external. For each interface, specify the information as listed below. Once every interface is described, this instructive table shall be removed from this document. |  |
| :---- | :---- |
| Name of software Owner of software, if external Details of interface Type of interface, such as interfaces to other software units Description of operational implications of data transfer, including security considerations | Data transfer requirements to and from the software unit, including data content, format, and sequence Formats of data for both the sending and receiving systems, including the data item names, codes, or abbreviations that are to be interchanged Interface procedures Interface equipment Data conversion requirements |

\<Enter your text here. Use a separate paragraph for each interface.\>

3. ### Dependencies {#dependencies-1}

| List the internal and external dependencies that will need to be managed. For every dependency, follow the guidance from the itemized list in this instructive table. Once every dependency is described, this instructive table shall be removed from this document. |  |
| :---- | :---- |
| What team or external company are you dependent on? What do you need them to deliver? Document interfaces with other teams or companies in Section 3.2.2. Note the relevant sub-section(s) in the “Ref” column of the table below. | Make sure teams fulfilling your dependencies understand the requested deliverable and have committed to it.  Track commitment in the table below. If you have dependencies on other teams or companies, document and track your integration plan in Section 3.2.3.1 below. |

\<If you want to add an introduction, replace this text. Otherwise, remove this paragraph.\>

| Team or Company | Deliverable | Ref | Commit? |
| :---- | :---- | :---- | :---- |
|  |  |  |  |
|  |  |  |  |

1. #### Integration Validation Plan {#integration-validation-plan}

\<Enter your text here. If you listed dependencies above, describe how you will validate integration with those dependencies. What specific functionality will you validate to prove out the integration? What interfaces will be covered by each validation item (can use section references)? Note when each validation is completed. Once done, remove this paragraph\>.

\<This level of validation need not cover every usage case or include robust error handling, though it should exercise all interfaces to your dependencies to some degree. This isn’t QA, it’s design validation to ensure that the interfaces are sufficient to meet requirements and are roughly functional. Devise your integration validation plan to meet those goals. Once done, remove this paragraph.\>

\<Integration validation should be completed in advance of code complete. Integration is a frequent source of disconnects and risk, and the goal here is to discover these risks as early as possible, before they become more costly to address. Once done, remove this paragraph.\>

\<Enter your text here.\>

| Functionality to Validate | Teams Participating | Interfaces Covered | Date Complete |
| :---- | :---- | :---- | :---- |
|  |  |  |  |
|  |  |  |  |

3. ## Dynamic Design {#dynamic-design}

   1. ### Functionality and Behavior {#functionality-and-behavior}

\<Enter your text here. Describe the functionality and behavior of the design or software component.\>

2. ### Control Flow {#oct.5,-2023}

\<Enter your text here. Usage of sequence diagram to describe the control flow is strongly recommended.\>

3. ### Data Flow {#data-flow}

\<Enter your text here. Usage of sequence diagram to describe the data flow is strongly recommended.\>

4. ### Error Handling {#error-handling}

\<Enter your text here. Describe general error handling routines and procedures. This may include, but not limited to, standard screen and database errors. Also, describe how the system will process application errors and any subsequent interaction with users.

\<Enter your text here. Include or reference standards or conventions that will be used (if any; otherwise, remove this paragraph).\>

\<Enter your text here. Describe in detail how error logs and data are secured and isolated from tampering (for example, a kernel driver should not write a log entry that any user can read as it may contain critical information that should not be publicly available.\>

5. ### Logging and Debugging {#logging-and-debugging}

\<Enter your text here. Describe the logging and debugging design for the software component.\>

6. ### State Machine {#state-machine}

\<Enter your text here. If applicable, show the state machine diagram.\>

4. ## Security Design {#security-design}

\<Enter your text here. If applicable, describe the security design which shall satisfy the security requirements. Ideally a thread model will be described which shows assets, relationships, and dataflow.\>

5. ## Test Automation {#test-automation}

\<This section is mandatory only for GPU Driver features, optional otherwise.\>

| Describe how the Test Automatability Requirements in the Requirements Document have been met. Consider the references itemized in the left column. Describe how the feature test automation has been met. Consider the directions itemized in the right columns. Once every aspect of automated testing is described, this instructive table shall be removed from this document. |  |
| :---- | :---- |
| [Open box Testing](https://confluence.nvidia.com/display/SWGPU/Test+Automatability+Checklist+for+WhiteBox+Testing) [Closed box Testing](https://confluence.nvidia.com/pages/viewpage.action?pageId=228661945) [Automating security in the CI/CD process](https://wiki.nvidia.com/engwiki/index.php/SW_Security/Security_Solutions/CC%2B%2B#Automation_Hooks) [Compiler and code sanitization](https://wiki.nvidia.com/engwiki/index.php/SW_Security/Security_Solutions/CC%2B%2B#Address_Sanitizer) [Fuzzing](https://wiki.nvidia.com/engwiki/index.php/SW_Security/Security_Solutions/Fuzzing) [Security-focused testing](https://wiki.nvidia.com/engwiki/index.php/SW_Security/Security_Solutions/Security_Tests) [Offensive security](https://wiki.nvidia.com/engwiki/index.php/SW_Security/Offensive_Security) | If applicable, stubs/mockup/API should be provided to enable test automation Provide any existing tools and methods useful for test automation. Describe how to test control flow, data flow and states transition using automation Provide and/or describe analytics capability Describe how the design accommodates security and performance testing necessary for this feature |

\<Enter your text here. Use a separate paragraph for each group of tests. Consider everything from the instructive table when describing those. Include links to automated tests for the code that provides test automatability hooks.\>

\<Alternatively, if there are valid technical/architectural reasons for why test automatability cannot be instrumented, a description must be provided as to these reasons, and there must be signoff granted by the SWQA test plan owner (to be agreed upon in conjunction with SWQA A\&T lead and SWQA WBT lead)\>.

6. ## Other Design Considerations {#other-design-considerations}

   1. ### Resource Limits {#resource-limits}

\<Describe resource consumption requirements for the design/module. The requirements can be listed a table format. See example table below. If you prefer to use it, remove this paragraph and enter you text in the table, otherwise enter your text here and remove the example table.\>

| Software Module | Resource Name | Requirement |
| :---- | :---- | :---- |
|  | Storage space (RAM, GPU DRAM, ROM, EEPROM, Flash memory, etc.) |  |
|  | Memory bandwidth utilization (e.g. DRAM) |  |
|  | PCIe utilization |  |
|  | Communication resources |  |
|  | Power consumption |  |
|  | GPU resources (Number of Cores, Number of Hardware Channels, Load) |  |
|  | Resource limits of software elements used by this element (e.g., number of textures, streams, etc.) |  |

2. ### High Availability {#high-availability}

\<Enter your text here. Describe how high availability is achieved.\>

3. ### Scalability {#scalability}

\<Enter your text here. Describe how the design handles scalability.\>

4. ### Future Work {#future-work}

\<Enter your text here. Describe how the design will evolve over time, especially if the design documented so far is less than ideal due to assumptions made.\>

## Appendix: Documentation Control for This Template

(Maintained by the PLC team and should be removed in the project work product document)

Documentation Control for This Template (Maintained by the PLC team)

| Item | Description |
| :---- | :---- |
| Title | Document Template for Controlled Docs |
| Author(s) | Arieh Cimet, Chong Teoh |
| Revision | July 6, 2022 |
| PLC Doc \# | SWE-PLC-L1-002-BaselinePLC-SADD-TMPL |
| State | Baseline |
| Approver list | Hiren Upadhyay |
| Reviewer list | PLC Stakeholders (should include QA Lead, Automation Lead, WBT lead) |
| Reviewed In | Review Meeting, NVBugs |

Revision History of this Template (Maintained by the PLC team)

| Revision | Author | Summary of Change |
| :---- | :---- | :---- |
| Nov 21, 2017 | Arieh Cimet, Chong Teoh | Initial version (based on SWE-SWCD-001-TMPL.docx by Dariusz Tomaszewski) |
| Jan 25, 2018 | Chong Teoh | Added Documentation Control table for this template |
| Jan 30, 2018 | Chong Teoh | Added Documentation Control and Revision History for document |
| Jan 31, 2018 | Chong Teoh | Converted template per SWE-DOC-005-TMPL Addressed review comments |
| Feb 7, 2018 | Chong Teoh | Addressed review comments per [NVBug 2054769](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/2054769), review meeting, and comments in template |
| Feb 15, 2018 | Ajay Keswani | Added Scoring Guidelines |
| April 4, 2018 | Chong Teoh | Fixed formatting per review feedback. Removed color. |
| June 5, 2018 | Ajay Keswani | Added Clarification that Template Documentation control is maintained by the PLC team Removed SWE Doc\# from Documentation control Removed scoring guidelines as these will be provided on confluence |
| October 18, 2018 | Ajay Keswani | Jean-Marc helped fix formatting glitches to import to Google Docs. |
| Aug 20, 2019 | Ajay Keswani | Added section 3.5 on test automatability |
| Aug 29, 2019 | Ajay Keswani | Updated to provide link to automated tests for code that provides test automatability hooks |
| Jan 24, 2020 | Ajay Keswani | Added more specifics under section 3.2 for Dependencies |
| Feb 08, 2021 | Hiren Upadhyay | Changed title from “PLC-L1: Software Project Plan” to “PLC-L1: Baseline PLC-L1: Software Architecture and Design Document” in order to differentiate this template from the one for Cloud PLC. Changed template file name from “SWE-PLC-L1-005-SPP-TMPL” to “SWE-PLC-L1-005-BaseLinePLC-SPP-TMPL”. |
| July 6, 2022 | Andrija Bosnjakovic, Jesper van Engelen, Puquan Xu | Reformatted the template to make the styles consistent – eliminated manual formatting. This is to address [NVBug 3471751](https://nvbugswb.nvidia.com/NvBugs5/SWBug.aspx?bugid=3471751&cmtNo=). Moved the Doc Control tables for this template to the Appendix. Also contributed by Eric Hall, Kabriel Robichaux, Renaud Gaubert, and Hiren Upadhyay. |
| Sept 1, 2023 | Puquan Xu, Hiren Upadhyay | Added NOTE under Reviewers section. |
| Oct.5, 2023 | Puquan Xu | Address Nvbug 3314702: Change Whitebox and Black Box to Open Box and Closed Box respectively. Also refer to [ReLingo Home \- ReLingo \- Confluence (nvidia.com)](https://confluence.nvidia.com/pages/viewpage.action?spaceKey=ReLingo&title=ReLingo+Home#ReLingoHome-Searchfortheseterms:). |


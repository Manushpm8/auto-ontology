# \<Name of Project or Feature\>

## PLC-L1: Basic PLC: Software Requirements Document

Documentation Control

| Item | Description |
| :---- | :---- |
| Title | \<doc title\> |
| Author(s) | \<author\> |
| Revision | \<date\> |
| State | \<Development, Baselined, Released for Approval, Approved\> |
| Reviewed in | \<tool\> |
| PLC-L1 SRD Template Revision | SWE-PLC-L1-001-BasicPLC-SRD-TMPL, Mar. 09, 2026 |

Approvers

| Date | Name | Notes |
| :---- | :---- | :---- |
| \<month dd, yyyy\> | \<Full Name\> | \<Brief description.\> |
|  |  |  |

Reviewers

(NOTE: Please ensure that reviewers include:

* impacted BUs (Business Units) or other NVIDIA product teams, as needed.  
* internal representatives for customers or partners (e.g. ISVs) to ensure that requirement or API changes do not impact applications.)

| Date | Name | Notes |
| :---- | :---- | :---- |
| \<month dd, yyyy\> | \<Full Name\> | \<Brief description.\> |
|  |  |  |

  Revision History

| Date | Author | Summary of Change |
| :---- | :---- | :---- |
| \<month dd, yyyy\> | \<Full Name\> | \<Brief description.\> |
|  |  |  |

# Introduction

## Overview

**Why is the project being developed?**  
\<Enter your text here.\>

**What problem does it solve?**  
\<Enter your text here.\>

**What are the main functions of the project?**  
\<Enter your text here.\>

**Who are the primary stakeholders?**  
\<Enter your text here.\>

**Who is developing the project?**  
\<Enter your text here.\>

## Assumptions

\<Enter your text here. List the assumptions used in the project definition and architecture.\>

## Constraints

\<Enter your text here. List the constraints that the project will face during development (for example: platforms to be used, development environment, legacy software needed, etc.)\>

## Dependencies

\<Optional: enter explanatory text before the table.\>

\<Populate the table below. List the internal and external dependencies that will need to be managed. What team or external company are you dependent on? What do you need them to deliver?\>

| Team or Company | Deliverable |
| :---- | :---- |
|  |  |
|  |  |

## Definitions, Acronyms, Abbreviations

| Term or Abbreviation | Description |
| :---- | :---- |
|  |  |
|  |  |

## References

| SWE Number | Input Work Product | Revision | Location |
| :---- | :---- | :---- | :---- |
| \<swe no\> | \<input document title\> | \<revision date or \#\> | \<[link](https://www.nvidia.com/en-us/about-nvidia/)\> |
|  |  |  |  |

# Use Cases

The following table lists the use cases for this project/feature.

| Use Case | Description |
| :---- | :---- |
| \<use case name\> | \<use case description\> |
|  |  |

# Requirements

| Feature owners and reviewers shall consider the following Requirement Types. See [guidelines](https://confluence.nvidia.com/display/RP/PLC+Guidelines%3A+Requirement+Types) for the descriptions. All types that apply to the feature shall be documented in the 4-column table below; once it gets populated, this instructive table shall be removed from this document.  |  |  |
| :---- | :---- | :---- |
| Functional Requirements Non-Functional Requirements Interface Requirements Safety Requirements | KPI Requirements Platform Requirements Security Requirements Legal and Standards Requirements | Telemetry Requirements Backward Compatibility Requirements Virtualization Requirements Test Automatability Requirements |

The following table lists the requirements for this project/feature. (GPU Driver uses the Enhanced Requirements Format in the second table below).

| ID | Title | Description | Type |
| :---- | :---- | :---- | :---- |
| REQ-1 | Ability to toggle between transparent and non-transparent mode | Source shall be able to toggle between transparent and non-transparent modes based on policy decisions in the client. | Functional |
| REQ-2 | Support for up to 8 LTTPRs | Source shall be able to support up to 8 LTTPRs without causing system or display hang | KPI |
| REQ-3 | Support for Windows and Linux | Support for LTTPR shall be available on all windows OS version (Windows 10 RS1, RS2, RS3) and Linux builds | Platform |
| REQ-4 | Support for SWQA automation | Support for feature automation shall be available on all applicable OS/platforms | Automatability |

\<Enhanced Requirements Format (Mandatory for GPU Driver Releases)\>

| ID | Title | Description | Type | QA Applicable | Use Case Scenario | Verification Criteria | Tools Required for Verification |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| REQ-1 | Ability to toggle between transparent and non-transparent mode | Source shall be able to toggle between transparent and non-transparent modes based on policy decisions in the client. | Functional | Yes/No | With P-State viewer launched and in listening mode launch balls.exe | Observe the P-state level at the time of launch and after 30 seconds. P-state should go to P0 while running balls.exe | nvPstateview.exe path Balls.exe path |
| REQ-2 | Support for up to 8 LTTPRs | Source shall be able to support up to 8 LTTPRs without causing system or display hang | KPI | Yes/No | Use Case Scenario for req. 2 | Verification Criteria for req.2 | Relevant tools needed for verification including its path |
| REQ-3 | Support for Windows and Linux | Support for LTTPR shall be available on all windows OS version (Windows 10 RS1, RS2, RS3) and Linux builds | Platform | Yes/No | Use Case Scenario for req.3 | Verification Criteria for req.3 | Relevant tools needed for verification including its path |
| REQ-4 | Support for SWQA automation | Support for feature automation shall be available on all applicable OS/platforms | Automatability | Yes/No | Use Case Scenario for req. 4 | Verification Criteria for req.4 | Relevant tools needed for verification including its path |
| REQ-5 | Verify P-States goes to P0 while running OGL application | Source shall be able to verify the P-States while running OGL application | Functional | Yes | With P-State viewer launched and in listening mode launch balls.exe | Observe the P-state level at the time of launch and after 30 seconds. P-state should go to P0 while running balls.exe | nvPstateview.exe path Balls.exe path |

# Test Automatability (Mandatory for GPU Driver Releases)

\<Read this section, and then reuse or replace its contents, as applicable. Replace this text with an overview of this chapter or remove this paragraph.\>

## Security implications for test automatability

In order to facilitate the ability for automation in a given feature, there are concerns that need to be addressed in the interest of security. While requirements for test domains have been clearly specified, those requirements must be met in a manner that does not compromise the overall or modular integrity of our software. 

Adding debug and test hooks should be instrumented in a method that does not provide additional attack surface to our product. Existing, tested and reviewed implementations should be favored for use/re-use for cost considerations, provided there are no known critical issues in those designs that cannot be mitigated.

Ideally hooks are not exposed in production, obfuscation is not a substitute for security and is not considered sufficient. Secure methods like FILE ACL’s, authentication etc. should be preferred for any significant functionality and interfaces.

## Security Best Practices

Wherever possible, automation hooks/testing should be done through standard interfaces (i.e., don’t build special where current already works). This limits the expansion of attack surface (minimize attack surface), and reduces the incidence of little used code paths, which themselves need to be tested and hardened which becomes a circular problem.

DO NOT alter trust relationships – if the “mouse click” can do A/B/C, the API for ‘testing’ mouse clicks shouldn’t be able to do more. (least privilege). Automation needs to preserve system trust relationships – process isolation, file access control, etc. – and should not subvert expected isolation guarantees.  Collecting global statistics or information across clients/processes generally needs to be admin level or better.

If it does need to do more, then you need to have the API do appropriate checks for admin/ACL’s or other authorization to protect the interface/trust boundary (authenticate and authorize).

This may require looking at where that code is being delivered because in some scenarios there may be contractual or other constraints that require that code not exist (e.g., safety environments) in the delivered production code. Consider all the places your code may be deployed. (A GPU can be deployed in consumer, with a VGPU stack in the cloud, embedded in an Auto context – each of these has different trust and isolation expectations that must be accounted for).

Any such API/interfaces need to be transparent to the customer, no backdoors etc. that would introduce difficulty with contracts or be perceived as malicious.

Validate and bound all inputs, do not process/execute arbitrary user-controlled input – i.e., don’t build in a privilege command shell or java parser in your “TEST” harness (strong input validation).

## Test Automatability Requirements Checklist

| Requirement | Type | Test Domain |
| :---- | :---- | :---- |
| If new API/Escape/RM control added or modified, need API definitions and header file | Must Have | Open Box |
| If new API/Escape/RM control added or modified, need information on HW configs where the API is valid and expected behavior on configs that are invalid | Must Have | Open Box |
| If new API/Escape/RM control added or modified, need valid data set and/or content to pass to the API | Must Have | Open Box |
| When APIs to GET state are not available/sufficient for validation, and validation must happen at HW register level, must have address of HW registers to be validated | Must Have | Open Box |
| When APIs to GET state are not available/sufficient for validation, and validation must happen at HW register level, must have matrix of valid values with scenarios to validate for | Must Have | Open Box |
| For APIs under test, recommended invalid data set and content to pass to the API | Nice to Have | Open Box |
| For APIs under test, list of all error conditions to test for | Nice to Have | Open Box |
| If visual artifacts to be checked, enable markers in logging where possible that can be used to predict occurrence of artifacts | Nice to Have | Open Box |
| Verbose logging to validate functionality implemented in the feature (e.g. Nvapiswak, swak, wpp, capturecore) | Must Have | Closed Box Automation |
| For any new UI component or change in existing UI specs, UI elements need to be accessibly by Spy tools and support control patters (for example: using MSFT tools) | Must Have | Closed Box Automation |
| For any new UI component or change in existing UI specs, keystroke combinations or other backdoor methods to automate the UI operations where UI elements are not exposed must be made available | Must Have | Closed Box Automation |
| Backdoor methods as an alternative to steps needing human inputs (example: LED status check, physical button press on panels, etc.) | Must Have | Closed Box Automation |
| New feature must have a test app (in house/3rd party), for example demos, sample application, benchmark, APIC which has automation hooks exposed via UI or backdoor methods | Must Have | Closed Box Automation |
| If there are visual checks (FPS counters, overlays, etc.) while running test apps, markers must be enabled in logging for verification | Must Have | Closed Box Automation |
| For performance testing, need instrumented code that provides timing markers in output logs | Nice to Have | Closed Box Automation |
| For compatibility tests involving 3rd party ISV applications, need scripting interfaces for automation via ISV or UI elements accessible by spy tools | Nice to Have | Closed Box Automation |

## Appendix: Documentation Control for This Template

(Maintained by the PLC team and should be removed in the project work product document)

Documentation Control for This Template (Maintained by the PLC team)

| Item | Description |
| :---- | :---- |
| Title | Document Template for Controlled Docs |
| Author(s) | Arieh Cimet, Chong Teoh |
| Revision | July 6, 2022 |
| PLC Doc \# | SWE-PLC-L1-001-BaselinePLC-SRD-TMPL |
| State | Baseline |
| Approver list | Hiren Upadhyay |
| Reviewer list | PLC Stakeholders (inclusive of QA leads \+ Automation and WBT leads) |
| Reviewed In | Review Meeting, NVBugs |

Revision History of this Template (Maintained by the PLC team)

| Revision | Author | Summary of Changes |
| :---- | :---- | :---- |
| Nov 21, 2017 | Arieh Cimet, Chong Teoh | Initial version |
| Jan 15, 2018 | Chong Teoh | Added Requirement Type guideline |
| Jan 25, 2018 | Chong Teoh | Added Documentation Control table for this template |
| Jan 30, 2018 | Chong Teoh | Added Documentation Control and Revision History for document |
| Jan 31, 2018 | Chong Teoh | Converted template per SWE-DOC-005-TMPL |
| Feb 7, 2018 | Chong Teoh | Addressed review comments per [NVBug 2054769](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/2054769), review meeting, and comments in template |
| Feb 15, 2018 | Ajay Keswani | Added Scoring Guidelines |
| April 4, 2018 | Chong Teoh | Fixed formatting per review feedback. |
| June 5, 2018 | Ajay Keswani | Added Clarification that Template Documentation control is maintained by the PLC team Removed SWE Doc\# from Documentation control Provided a confluence pointer to Requirement types Removed scoring guidelines as these will be provided on confluence |
| Aug 20, 2019 | Ajay Keswani | Adding Test Automatability Requirements in Section 4 |
| Jan 24, 2020 | Ajay Keswani | Adding more specifics under section 1.2 for internal and external dependencies. |
| Feb 8, 2021 | Hiren Upadhyay | Changed title from “PLC-L1: Software Requirements Document” to “PLC-L1: Baseline PLC-L1: Software Requirements Document” in order to differentiate this template from the one for Cloud PLC. Changed template file name from “SWE-PLC-L1-001 \-SRD-TMPL” to “SWE-PLC-L1-001-BaseLinePLC-SRD-TMPL”. |
| July 6, 2022 | Andrija Bosnjakovic, Jesper van Engelen, Puquan Xu | Reformatted the template to make the styles consistent – eliminated manual formatting. This is to address [NVBug 3471751](https://nvbugswb.nvidia.com/NvBugs5/SWBug.aspx?bugid=3471751&cmtNo=). Removed the section of “APPENDIX A – REQUIREMENT TYPE GUIDELINE” as it’s a duplication of Section 3\. Moved the Doc Control tables for this template to the Appendix. Also contributed by Eric Hall, Kabriel Robichaux, Renaud Gaubert, and Hiren Upadhyay. |
| Sept 1, 2023 | Puquan Xu, Hiren Upadhyay | Added NOTE under Reviewers section. |
| Oct.5, 2023 | Puquan Xu | Address Nvbug 3314702: Change Whitebox and Black Box to Open Box and Closed Box respectively. Also refer to [ReLingo Home \- ReLingo \- Confluence (nvidia.com)](https://confluence.nvidia.com/pages/viewpage.action?spaceKey=ReLingo&title=ReLingo+Home#ReLingoHome-Searchfortheseterms:). |
| Mar.09, 2026 | Puquan Xu, Gururaghavendra Reddy | Added an Enhanced Requirements Format table for the GPU Driver SW with additional columns for QA related inputs. Reviewed in meetings and email with the GPU driver team. |


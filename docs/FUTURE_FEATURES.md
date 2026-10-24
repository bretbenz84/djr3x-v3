# Future companion features

September 17, 2026 user direction. **Roadmap only; no implementation or hardware
operation authorized by this document.** Preserve V2's enjoyable activities while
using V3's shared conversation, evidence, memory and execution architecture.
These additions follow the core reliability/cost work and applicable
[operator-tool migration](OPERATOR_TOOLS_MIGRATION.md) and
[optional-hardware installation](PUBLIC_INSTALL_PLAN.md) prerequisites.

## Requested future features

| Feature | Intended V3 experience | Integration boundary |
| --- | --- | --- |
| Existing Jeopardy game | Bring back the familiar game, including its useful rules, scoring and presentation. Rex can host naturally and handle questions, interruptions and resumption. | Reuse V2's game knowledge and proven behavior; explicit game state owns scores, answer windows and progression. One conversation/output owner handles language and actual delivery. Do not transplant an independent conversation loop. |
| On-device voice-cloning / impersonation bits | **Implemented October 6, 2026** ([voice impressions](IMPERSONATION.md)): requested impressions of famous voices, known people and the current speaker, and rare spontaneous cameos, all chosen and written by the conversation model through the `impersonate` tool and spoken by the local Breeze engine. | Live acceptance on the robot (playback quality, time to first cloned audio under the full workload, how often cameos land) remains open. Parody stays distinct from facts, original voice samples and identity evidence. |
| Room exploration | Rex can look around, investigate unfamiliar things and build useful remembered observations about his surroundings. | **Implemented October 11, 2026** ([room recognition](ROOM_RECOGNITION.md)): V2's place model recognizes rooms, Rex asks and learns unfamiliar ones, remembers which rooms connect, and an `explore` goal drives and looks around. Live acceptance pending; navigation between rooms is not built. |
| Autonomous motion | Eventually choose purposeful movement as part of attention, exploration and social interaction, beyond explicit drive commands. | September 27: bounded LLM-selected base plans, vision seek of described things, radar priors and self-initiated moves are implemented and floor-tested ([drive base](DRIVE_BASE_INTEGRATION.md)). Roaming, navigation, mapping, localization and compass heading still do not exist. |
| Interest-aware idle banter and news | As Rex learns a person's interests, bring up relevant events, callbacks and follow-ups naturally. | **Implemented October 11, 2026** ([details](OBSERVATION_SOURCES.md#searched-daily-stories-and-interest-news-october-11-2026)): stated interests saved by `remember_interest`, daily topic searches while the person is here, offered through the shared proactive owner. Live conversational quality is unaccepted; interest-aware banter beyond news is not built. |
| “Do you ever think” | After a long lull, spark conversation with a fresh LLM-generated hypothetical, such as everyone winning the lottery on the same day. | **Implemented October 23, 2026**: 45 quiet seconds (Rex included) with someone in view bring one question per quiet stretch, as a beat of his current goal; heard questions are remembered so the next differs. Live acceptance pending. [Feature details](PROACTIVE_CONVERSATION.md#do-you-ever-think-implemented-october-23) |

## Personalization should develop through conversation

- Learn durable interests from source-linked user statements and clear feedback.
  A passing mention, a question about a subject, or Rex's own joke does not establish
  an interest. Keep uncertain speaker attribution and the mentioned subject separate.
- Use accepted interests to select useful topics and, eventually, retrieve relevant
  news. For example, someone who has expressed an interest in space might enjoy a
  launch update; the same headline need not be offered to everyone else.
- Consider who is actually present, the current topic, prior heard remarks, interest
  freshness and whether the moment welcomes an interruption. Unknown identity need
  not prevent general banter, but must not silently choose a person's profile.
- Let people redirect, correct or decline a topic. Follow-ups should reflect actual
  exchanges and delivery, not promises Rex never finished speaking.
- Keep variety: news, shared memories, relevant questions and light observations.
  Knowing one interest must not reduce every conversation to that subject. Silence
  remains a legitimate choice.
- Cache and deduplicate external information; bound refreshes and hosted spending.
  Tailoring should use the existing conversational decision wherever possible,
  without adding a mandatory classifier call before ordinary replies.

## Current versus future

V3 already has proactive arrival/lull opportunities, quiet/resume, memory
opportunities, sourced general news/weather and scene inputs. Their live social
quality remains under evaluation. Interest news (October 11) covers the first part of the personalization above;
learning interests from feedback and choosing topics beyond news remain future work.
Jeopardy remains a future integration (room exploration landed October 11) (local impressions landed October 6);
bounded purposeful base motion is implemented and in floor testing. Motion-aware noticing is also still the documented
[future idea](PROACTIVE_CONVERSATION.md#future-idea-motion-and-new-arrivals-as-attention-cues).

V2 reference starting points: `features/jeopardy.py`,
`docs/jeopardy_live_reliability_review.md`, `docs/exploration_mode_plan.md`,
`hardware/motion.py`, and `awareness/current_events.py`. Recheck active code,
working-tree status and measured behavior before each extraction; V2 plans are not
proof of implemented or reliable behavior. The remaining features copy no V2 code or
personal data yet; the drive base, startup and motion sounds, and the impression
engine were extracted separately (see [V2_REFERENCE.md](../V2_REFERENCE.md)).

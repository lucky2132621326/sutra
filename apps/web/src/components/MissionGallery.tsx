/**
 * What Sūtra can actually do.
 *
 * The app opened on an empty canvas and three suggestions that were all the
 * same scenario, so the honest impression was "this registers you for a Google
 * internship". Behind that door are 24 tools across five agents, and none of
 * it was visible until you happened to ask the right question.
 *
 * This lives in the centre pane — the largest dead space in the app, and the
 * first place the eye lands — and is replaced by the run itself the moment
 * anything starts. So the emptiest screen becomes the most informative one.
 *
 * DISCIPLINE: every prompt here maps to a tool that exists in TOOL_REGISTRY.
 * A gallery that advertises something the backend cannot serve is a demo that
 * breaks the moment a judge clicks the interesting-looking card.
 */
import {
  ArrowRight, BookOpenCheck, BriefcaseBusiness, Building2,
  CalendarCheck2, Network, Scale, ShieldCheck,
  type LucideIcon,
} from 'lucide-react'

import { useStore } from '../state/store'

interface Mission {
  label: string
  prompt: string
  /** The tools this actually exercises — shown so the claim is checkable. */
  tools: string
  /** Marks the two runs that show several agents cooperating. */
  flagship?: boolean
}

interface Group {
  agent: string
  title: string
  blurb: string
  icon: LucideIcon
  missions: Mission[]
}

const GROUPS: Group[] = [
  {
    agent: 'academic',
    title: 'Academics',
    blurb: 'Your timetable, attendance and electives',
    icon: BookOpenCheck,
    missions: [
      { label: 'Attendance risk', tools: 'get_attendance · compute_attendance_eligibility',
        prompt: 'Which of my courses am I closest to failing on attendance?' },
      { label: 'This week', tools: 'get_timetable',
        prompt: "What's on my timetable this week?" },
      { label: 'Electives', tools: 'recommend_electives',
        prompt: 'Which electives would suit someone interested in machine learning?' },
    ],
  },
  {
    agent: 'placement',
    title: 'Placements',
    blurb: 'Eligibility, companies and preparation',
    icon: BriefcaseBusiness,
    missions: [
      { label: 'Am I eligible?', tools: 'check_placement_eligibility',
        prompt: 'Am I eligible for the Google internship?' },
      { label: 'Open to me', tools: 'list_companies · check_placement_eligibility',
        prompt: 'Which companies am I actually eligible to apply to right now?' },
      { label: 'Prep plan', tools: 'get_prep_plan',
        prompt: 'Build me a preparation plan for the Google interview.' },
    ],
  },
  {
    agent: 'events',
    title: 'Events',
    blurb: 'Workshops, capacity, clubs — and the one action that needs you',
    icon: CalendarCheck2,
    missions: [
      { label: 'Register me', tools: 'search_events · register_event (needs approval)',
        flagship: true,
        prompt: "I'm a third-year CSE student. Am I eligible for the Google internship? "
              + 'If yes, register me for the placement workshop, add it to my calendar, '
              + 'and remind me an hour before.' },
      { label: "What's on", tools: 'search_events',
        prompt: 'What AI workshops are coming up?' },
      { label: 'Find my people', tools: 'recommend_clubs',
        prompt: 'Suggest clubs related to machine learning.' },
    ],
  },
  {
    agent: 'knowledge',
    title: 'Policies',
    blurb: 'Answers quoted from the regulations, with the clause number',
    icon: Scale,
    missions: [
      { label: 'Exam eligibility', tools: 'search_policy',
        prompt: 'What attendance do I need to sit for exams?' },
      { label: 'If I fall short', tools: 'search_policy',
        prompt: 'What happens if I miss too many classes, and can it be condoned?' },
    ],
  },
  {
    agent: 'services',
    title: 'Campus services',
    blurb: 'Library, hostel, email, grievances, calendar',
    icon: Building2,
    missions: [
      { label: 'Makeup exam request', tools: 'search_policy · compute_attendance_eligibility · draft_email',
        flagship: true,
        prompt: 'Summarise the examination regulations, calculate my attendance eligibility, '
              + 'and draft an email requesting permission for a makeup exam.' },
      { label: 'My loans', tools: 'library_loans · renew_book',
        prompt: 'What library books do I have out, and when are they due?' },
      { label: 'Raise an issue', tools: 'file_grievance (needs approval)',
        prompt: 'The wifi in my hostel block has been down for a week. Can you raise this?' },
    ],
  },
]

export function MissionGallery() {
  const setDraft = useStore((s) => s.setDraft)
  const requestComposerFocus = useStore((s) => s.requestComposerFocus)

  const pick = (prompt: string) => {
    setDraft(prompt)
    requestComposerFocus()
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '24px 26px 34px' }}>
      <header className="mission-hero" style={{
        marginBottom: 22, padding: '22px 22px 18px', border: '1px solid var(--line)',
        borderRadius: 'var(--r-card)', background: 'var(--surface)', boxShadow: 'var(--e1)',
      }}>
        <div style={{ display: 'flex', gap: 18, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 380px', minWidth: 0 }}>
            <div className="eyebrow" style={{ color: 'var(--accent)', marginBottom: 6 }}>
              One request · five specialists
            </div>
            <h1 className="font-display" style={{
              fontSize: 27, lineHeight: '33px', margin: '0 0 8px', maxWidth: 600,
            }}>
              Start with a goal. Sūtra assembles the campus around it.
            </h1>
            <p style={{
              maxWidth: 630, fontSize: 13.5, lineHeight: '20px',
              color: 'var(--ink-600)', margin: 0,
            }}>
              Ask naturally. The orchestrator selects the right agents, checks their work
              against campus records and pauses before any action that changes something.
            </p>
          </div>

          <div aria-label="Sūtra safeguards" style={{
            flex: '0 1 210px', minWidth: 190, display: 'grid', gap: 8,
          }}>
            <Promise icon={Network} title="Agents collaborate" detail="Parallel when independent" />
            <Promise icon={ShieldCheck} title="You stay in control" detail="Approval before writes" />
          </div>
        </div>

        <div aria-label="How a Sūtra mission works" style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, minmax(110px, 1fr))',
          marginTop: 18, borderTop: '1px solid var(--line)', paddingTop: 14,
        }}>
          {[
            ['01', 'Ask a goal'],
            ['02', 'Agents investigate'],
            ['03', 'Conflicts resolved'],
            ['04', 'Approve & receive proof'],
          ].map(([number, label], index) => (
            <div key={number} style={{
              minWidth: 0, padding: '0 12px',
              borderLeft: index ? '1px solid var(--line)' : 'none',
            }}>
              <span className="mono" style={{ fontSize: 9.5, color: 'var(--accent)' }}>{number}</span>
              <span style={{
                display: 'block', marginTop: 2, fontSize: 11.5, lineHeight: '15px',
                fontWeight: 650, color: 'var(--ink-900)',
              }}>{label}</span>
            </div>
          ))}
        </div>
      </header>

      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        gap: 16, margin: '0 2px 10px',
      }}>
        <div>
          <div className="eyebrow">What can Sūtra do?</div>
          <div style={{ fontSize: 12, color: 'var(--ink-400)', marginTop: 2 }}>
            Choose a mission to place it in the composer, then edit it however you like.
          </div>
        </div>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-300)', whiteSpace: 'nowrap' }}>
          24 campus tools
        </span>
      </div>

      <div style={{
        display: 'grid', gap: 16,
        gridTemplateColumns: 'repeat(auto-fit, minmax(268px, 1fr))',
      }}>
        {GROUPS.map((group) => {
          const Icon = group.icon
          return (
          <section key={group.agent} className="mission-group" style={{
            border: '1px solid var(--line)', borderRadius: 'var(--r-card)',
            background: 'var(--surface)', overflow: 'hidden', boxShadow: 'var(--e1)',
          }}>
            <div style={{
              padding: '11px 13px 10px', display: 'flex', gap: 10, alignItems: 'center',
              borderBottom: '1px solid var(--line)',
              borderLeft: `3px solid var(--agent-${group.agent})`,
            }}>
              <span aria-hidden style={{
                width: 30, height: 30, flex: '0 0 auto', display: 'grid', placeItems: 'center',
                borderRadius: 'var(--r-chip)', color: `var(--agent-${group.agent})`,
                background: 'var(--surface-sunken)',
              }}>
                <Icon size={16} strokeWidth={1.8} />
              </span>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{
                    fontSize: 13.5, fontWeight: 700, color: 'var(--ink-900)',
                  }}>{group.title}</span>
                  <span className="eyebrow" style={{
                    fontSize: 9, color: `var(--agent-${group.agent})`,
                  }}>{group.agent}</span>
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--ink-400)', marginTop: 1 }}>
                  {group.blurb}
                </div>
              </div>
            </div>

            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {group.missions.map((m, i) => (
                <li key={m.label}>
                  <button
                    onClick={() => pick(m.prompt)}
                    className="mission"
                    aria-label={`Use mission: ${m.label}`}
                    style={{
                      width: '100%', textAlign: 'left', cursor: 'pointer',
                      border: 'none', background: 'transparent',
                      borderTop: i === 0 ? 'none' : '1px solid var(--line)',
                      padding: '9px 13px', fontFamily: 'var(--font-body)',
                      display: 'block',
                    }}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-900)' }}>
                        {m.label}
                      </span>
                      {m.flagship && (
                        <span style={{
                          fontSize: 9, fontWeight: 700, letterSpacing: '0.05em',
                          textTransform: 'uppercase', color: 'var(--accent)',
                          background: 'var(--accent-weak)', padding: '1px 5px',
                          borderRadius: 'var(--r-pill)',
                        }}>multi-agent</span>
                      )}
                    </span>
                    <span style={{
                      display: 'block', fontSize: 11.5, lineHeight: '16px',
                      color: 'var(--ink-600)', marginTop: 3,
                    }}>
                      {m.prompt.length > 96 ? `${m.prompt.slice(0, 96)}…` : m.prompt}
                    </span>
                    {/* Naming the tools keeps the gallery a claim you can check
                        against the code, not marketing copy. */}
                    <span className="mono" style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      gap: 8, fontSize: 9.5, color: 'var(--ink-300)', marginTop: 5,
                    }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {m.tools}
                      </span>
                      <ArrowRight size={13} aria-hidden style={{ flex: '0 0 auto', color: 'var(--accent)' }} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )})}
      </div>
    </div>
  )
}

function Promise({ icon: Icon, title, detail }: {
  icon: LucideIcon
  title: string
  detail: string
}) {
  return (
    <div style={{
      display: 'flex', gap: 8, alignItems: 'center', padding: '8px 9px',
      border: '1px solid var(--line)', borderRadius: 'var(--r-chip)',
      background: 'var(--surface-sunken)',
    }}>
      <Icon size={16} aria-hidden style={{ color: 'var(--accent)', flex: '0 0 auto' }} />
      <span style={{ minWidth: 0 }}>
        <strong style={{ display: 'block', fontSize: 11.5, lineHeight: '14px' }}>{title}</strong>
        <span style={{ display: 'block', fontSize: 10.5, lineHeight: '14px', color: 'var(--ink-400)' }}>{detail}</span>
      </span>
    </div>
  )
}

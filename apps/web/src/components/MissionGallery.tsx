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
  missions: Mission[]
}

const GROUPS: Group[] = [
  {
    agent: 'academic',
    title: 'Academics',
    blurb: 'Your timetable, attendance and electives',
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
    <div style={{ height: '100%', overflowY: 'auto', padding: '28px 30px 34px' }}>
      <header style={{ marginBottom: 22, maxWidth: 620 }}>
        <h2 className="font-display" style={{ fontSize: 24, lineHeight: '30px', margin: '0 0 6px' }}>
          What Sūtra can do
        </h2>
        <p style={{ fontSize: 13.5, lineHeight: '20px', color: 'var(--ink-600)', margin: 0 }}>
          Five specialist agents, 24 tools, one conversation. Pick anything below and it
          fills the composer — or just ask in your own words. Anything that writes stops
          for your approval first.
        </p>
      </header>

      <div style={{
        display: 'grid', gap: 16,
        gridTemplateColumns: 'repeat(auto-fit, minmax(268px, 1fr))',
      }}>
        {GROUPS.map((group) => (
          <section key={group.agent} style={{
            border: '1px solid var(--line)', borderRadius: 'var(--r-card)',
            background: 'var(--surface)', overflow: 'hidden',
          }}>
            <div style={{
              padding: '10px 13px 9px',
              borderBottom: '1px solid var(--line)',
              borderLeft: `3px solid var(--agent-${group.agent})`,
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{
                  fontSize: 13.5, fontWeight: 700, color: 'var(--ink-900)',
                }}>{group.title}</span>
                <span className="eyebrow" style={{
                  fontSize: 9.5, color: `var(--agent-${group.agent})`,
                }}>{group.agent} agent</span>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-400)', marginTop: 2 }}>
                {group.blurb}
              </div>
            </div>

            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {group.missions.map((m, i) => (
                <li key={m.label}>
                  <button
                    onClick={() => pick(m.prompt)}
                    className="mission"
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
                      display: 'block', fontSize: 9.5, color: 'var(--ink-300)', marginTop: 4,
                    }}>
                      {m.tools}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  )
}

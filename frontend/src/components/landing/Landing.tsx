/* eslint-disable @next/next/no-img-element -- four small, pre-sized archival JPEGs served as static files */
import "./landing.css";

import { Earth } from "./Earth/Earth";
import { Orb } from "./Orb/Orb";
import { ReceiptDemo } from "./ReceiptDemo";
import type { Receipt } from "./receipt";

// Demo, API docs and MCP endpoint links are placeholders until S3 gives URLs.
const DEMO = "#cta";
const SOON = "#top";

const NAV = [
  ["How it works", "#how"],
  ["The receipt", "#receipt"],
  ["Plans", "#plans"],
  ["Questions", "#faq"],
] as const;

const STAGES = [
  ["a", 7, "7 minutes", "First, three agents vote without seeing each other."],
  ["b", 12, "12 minutes", "Then a team lead signs off."],
  ["c", 60, "Up to an hour", "Then a manager, before the timeout you set."],
  ["d", 60, "Up to an hour", "Last, an org admin. A late step moves up, never down."],
  ["e", 6, "Instant", "Receipt."],
] as const;

const RULES = [
  ["head", "", ""],
  ["t1", "Approve from Telegram", "Pro and Enterprise. The button runs the same checks as the web app."],
  ["t2", "Blind first round", "Agents vote before they see each other’s votes, so one confident answer cannot pull the rest."],
  ["t3", "Tenants stay apart", "Another organization’s id returns 404, never 403."],
  ["t4", "Unknown means careful", "New action types never auto-approve."],
  ["t5", "Signed webhooks", "Retried five times, then parked."],
  ["t6", "One person, one level", "Nobody approves a whole chain alone."],
  ["t7", "Edits to the log", "The audit trail is append-only."],
  ["t8", "Minutes, then humans", "If too few reviewers show up, the request goes to people. It never approves itself."],
] as const;

const ART: Record<string, React.ReactNode> = {
  t1: <span className="ln-pillart"><span>A</span></span>,
  t2: (
    <>
      <span className="ln-disc ln-d1">?</span>
      <span className="ln-disc ln-d2">?</span>
      <span className="ln-disc ln-d3">?</span>
    </>
  ),
  t3: <span className="ln-big">404</span>,
  t4: <span className="ln-big">Medium</span>,
  t5: <span className="ln-big">v1,Zk3…</span>,
  t6: (
    <>
      <span className="ln-disc ln-e1" />
      <span className="ln-disc ln-e2" />
      <span className="ln-disc ln-e3" />
    </>
  ),
  t7: <span className="ln-big">0</span>,
  t8: <span className="ln-big">60</span>,
};

const COMMONS = "https://commons.wikimedia.org/w/index.php?search=";
const CC = "https://creativecommons.org/";
// [title, creator, [licence name, link] or ["", "public domain"], source (a Commons search for the title)]
const PICTURES = [
  ["Almond-shaped seal matrix", "Cleveland Museum of Art", ["CC0", `${CC}publicdomain/zero/1.0/`], `${COMMONS}almond-shaped+seal+Cleveland+Museum+of+Art`],
  ["Mexico 1901 notary sealing stamp", "unknown author", ["", "public domain"], `${COMMONS}notary+sealing+stamp+Mexico+1901`],
  ["Savings book", "Polish Postal Savings Bank", ["", "public domain"], `${COMMONS}savings+book+Pocztowa+Kasa+Oszczednosci`],
  ["Account-book of Thomas Mister", "Wellcome Collection", ["CC BY 4.0", `${CC}licenses/by/4.0/`], `${COMMONS}Account-book+of+Thomas+Mister+Wellcome`],
] as const;

const FAQ = [
  ["Scope", "Do you run my agents?", "No. Agent Ops never runs an agent. Yours send decision requests over MCP or REST, and read the result and the receipt back."],
  ["Trust", "What if all the reviewers agree?", "Agreement is not proof. Only low-risk actions that an admin has marked low skip the people, and an action type nobody has classified counts as medium."],
  ["Timeouts", "What if nobody answers?", "Every level has a timeout. An overdue step moves up to the next level, and after the last one the request expires. A timeout never approves."],
  ["Receipts", "How does my system know a request was approved?", "Your executing system checks the receipt through the API before it acts. If the request changed after approval, the check fails."],
  ["Data", "Is my data kept apart from other organizations?", "Every query is scoped to your organization and the database enforces it again with row-level security. Another organization’s ids return 404."],
] as const;

function Mark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <g fill="currentColor">
        <circle cx="4" cy="5" r="2.2" />
        <circle cx="4" cy="12" r="2.2" />
        <circle cx="4" cy="19" r="2.2" />
        <circle cx="19" cy="12" r="3.2" />
      </g>
      <path d="M6 5l10 7M6 12h10M6 19l10-7" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function Hero() {
  return (
    <section className="ln-hero" aria-label="Agent Ops">
      {/* Plain accent circle; the silk shader draws into it when WebGL is available. */}
      <div className="ln-orb" aria-hidden="true">
        <Orb />
      </div>
      <div className="ln-in ln-hero-in">
        <h1>
          Agents ask.
          <br />
          Humans sign off.
        </h1>
        <p className="ln-sub">
          Agents vote blind, people sign off level by level, and every approval ships a receipt that breaks if the request is
          edited.
        </p>
        <div className="ln-cta">
          <a className="ln-btn" href={DEMO}>
            Open the demo
          </a>
          <a className="ln-link" href="#how">
            See how a request moves
          </a>
        </div>
        <div className="ln-frag" data-ui>
          <div className="ln-slip">
            <div className="ln-slip-t">
              Refund order #48213 ($1,240.00)<span className="ln-pill">High</span>
            </div>
            <div className="ln-slip-m">
              <span className="ln-id">billing-agent</span> · escalates to the org admin in 49&nbsp;min
            </div>
            <ol className="ln-rail" aria-label="Quorum done, level 1 done, level 2 current, level 3 and receipt pending">
              {[
                ["d", "Quorum", "3 of 3", 100],
                ["d", "Level 1", "Team lead", 100],
                ["c", "Level 2", "You", 18],
                ["p", "Level 3", "Org admin", 0],
                ["p", "Receipt", "When approved", null],
              ].map(([state, name, who, fill]) => (
                <li className="ln-n" data-state={state} key={name}>
                  <i />
                  {fill !== null && (
                    <u>
                      <s style={{ width: `${fill}%` }} />
                    </u>
                  )}
                  <b>{name}</b>
                  {who}
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}

function Stages() {
  return (
    <section className="ln-how" id="how" aria-labelledby="how-h">
      <div className="ln-in">
        <h2 id="how-h">Votes, levels, receipt.</h2>
        <ol
          className="ln-stages"
          aria-label="A sample refund: votes take 7 minutes, the team lead 12 minutes, the manager up to an hour, the org admin up to an hour, then the receipt is instant"
        >
          {STAGES.map(([tone, g, label, clause]) => (
            <li className="ln-stage" key={clause} style={{ "--g": g } as React.CSSProperties}>
              <span className="ln-seg" data-tone={tone}>
                {label}
              </span>
              <span className="ln-clause">{clause}</span>
            </li>
          ))}
        </ol>
        <p className="ln-note">
          This is one sample high-risk refund. Low-risk actions that an admin has marked low and that every agent approves skip
          the people. Everything else climbs the levels, and nothing is ever approved by a timeout.
        </p>
      </div>
    </section>
  );
}

function Rules() {
  return (
    <section className="ln-feat" aria-labelledby="feat-h">
      <div className="ln-in ln-board">
        {RULES.map(([id, title, text]) =>
          id === "head" ? (
            <h2 className="ln-tile ln-t-head" id="feat-h" key={id}>
              Rules, not promises.
            </h2>
          ) : (
            <div className={`ln-tile ln-${id}`} key={id}>
              <div className="ln-art" aria-hidden="true">
                {ART[id]}
              </div>
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          ),
        )}
      </div>
    </section>
  );
}

function Plans() {
  return (
    <section id="plans" aria-label="Plans">
      <div className="ln-plans">
        <div className="ln-plan" data-plan="free">
          <img className="ln-pic" src="/landing/savings-book.jpg" width={230} height={174} loading="lazy" alt="A savings book with an empty table of entries, Poland, 1930s" />
          <h2>Free</h2>
          <p className="ln-who">For trying a team of agents on real decisions.</p>
          <p className="ln-incl">One approval level, up to three reviewers, one voting round. No attachments, Telegram or analytics.</p>
          <div className="ln-pfoot">
            <p className="ln-fig">
              50<small>requests a month</small>
            </p>
            <a className="ln-btn" href={DEMO}>
              Start on Free
            </a>
          </div>
        </div>
        <div className="ln-plan" data-plan="pro">
          <h2>Pro</h2>
          <p className="ln-who">For agents that act every day.</p>
          <p className="ln-incl">
            Three approval levels, up to seven reviewers, a second discussion round, attachments, Telegram approvals and analytics.
          </p>
          <div className="ln-pfoot">
            <p className="ln-fig">
              1,000<small>requests a month</small>
            </p>
            <a className="ln-btn ln-light" href={DEMO}>
              Try Pro in the demo
            </a>
          </div>
        </div>
      </div>
      <div className="ln-in ln-ent">
        <p>Enterprise has no request limit and adds full-text search, per-team policies and audit export.</p>
      </div>
    </section>
  );
}

function Faq() {
  return (
    <section className="ln-faq" id="faq" aria-labelledby="faq-h">
      <div className="ln-in">
        <h2 id="faq-h">Fair questions.</h2>
        <div className="ln-faqwrap">
          <div className="ln-cards">
            {FAQ.map(([tab, q, a], i) => (
              <details className="ln-card" name="faq" open={i === 0} key={q}>
                <summary>
                  <span className="ln-tab">{tab}</span>
                  {q}
                </summary>
                <p className="ln-ans">{a}</p>
              </details>
            ))}
          </div>
          <figure className="ln-fp">
            <img src="/landing/account-book.jpg" width={440} height={440} loading="lazy" alt="A page of an 18th-century account book with handwritten entries" />
          </figure>
        </div>
      </div>
    </section>
  );
}

function Cta() {
  return (
    <section className="ln-cta-row" id="cta" aria-labelledby="cta-h">
      <div className="ln-in ln-crow">
        <div className="ln-flank" data-side="l">
          <img src="/landing/notary-seal.jpg" width={360} height={360} loading="lazy" alt="A notary’s embossed star seal on a document, Mexico, 1901" />
        </div>
        <div className="ln-ctile">
          <h2 id="cta-h">Let agents ask first.</h2>
          <p>Open the demo as a team lead, a manager or an org admin. No Google account needed.</p>
          <div className="ln-row">
            <a className="ln-btn ln-light" href={SOON}>
              Open the demo
            </a>
            <a className="ln-link" href={SOON}>
              Read the API docs
            </a>
          </div>
        </div>
        <div className="ln-flank" data-side="r">
          <img src="/landing/almond-seal.jpg" width={360} height={360} loading="lazy" alt="A gilt almond-shaped seal matrix from the 14th century" />
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="ln-foot">
      <div className="ln-in">
        <svg className="ln-footmark" viewBox="0 0 1200 240" role="img" aria-label="Agent Ops: quorum, two levels, a pending level, a receipt">
          <g className="ln-m-on">
            <rect x="160" y="112" width="136" height="16" />
            <rect x="456" y="112" width="144" height="16" />
            <rect x="744" y="112" width="27" height="16" />
          </g>
          <g className="ln-m-off">
            <rect x="771" y="112" width="125" height="16" />
            <rect x="1040" y="112" width="60" height="16" />
          </g>
          <g className="ln-m-on">
            <circle cx="80" cy="120" r="80" />
            <circle cx="376" cy="120" r="80" />
            <rect x="1100" y="60" width="100" height="120" />
          </g>
          <circle className="ln-m-ring" cx="672" cy="120" r="64" />
          <circle className="ln-m-dash" cx="968" cy="120" r="66" />
        </svg>
        <div className="ln-cols">
          <div>
            <h3>Product</h3>
            <ul>
              {NAV.map(([l, h]) => (
                <li key={h}>
                  <a href={h}>{l}</a>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Developers</h3>
            <ul>
              <li><a href={SOON}>API docs</a></li>
              <li><a href={SOON}>MCP endpoint</a></li>
              <li><a href={SOON}>Webhooks</a></li>
            </ul>
          </div>
          <div>
            <h3>Try it</h3>
            <ul>
              <li><a href={DEMO}>Open the demo</a></li>
              <li><a href="/login">Sign in</a></li>
            </ul>
          </div>
          <div>
            <h3>Approvals</h3>
            <ul>
              <li>Agents ask.</li>
              <li>Humans sign off.</li>
              <li>Receipts break on edits.</li>
            </ul>
          </div>
        </div>
        <div className="ln-last">
          <div className="ln-credits">
            <p>
              Agent Ops. Pictures, all cropped and resized for this page, via Wikimedia Commons:
            </p>
            <ul>
              {PICTURES.map(([title, by, licence, source]) => (
                <li key={title}>
                  <a href={source}>{title}</a>, {by}, {licence[0] ? <a href={licence[1]}>{licence[0]}</a> : licence[1]}
                </li>
              ))}
            </ul>
            <p>
              Agent icon “ai agent” by Maassam Mohammed Anis, Noun Project. Land outlines: Natural Earth, public domain.
            </p>
          </div>
          <a className="ln-btn ln-light" href="#top">
            Back to top
          </a>
        </div>
      </div>
    </footer>
  );
}

export function Landing({ receipt }: { receipt: Receipt }) {
  return (
    <div className="ln">
      <a className="ln-skip" href="#top">
        Skip to content
      </a>
      <header className="ln-in">
        <nav className="ln-nav" aria-label="Main">
          <a className="ln-brand" href="#top">
            <Mark />
            Agent Ops
          </a>
          <ul>
            {NAV.map(([l, h]) => (
              <li key={h}>
                <a href={h}>{l}</a>
              </li>
            ))}
          </ul>
          <a className="ln-btn" href={DEMO}>
            Open the demo
          </a>
        </nav>
      </header>
      <main id="top">
        <Hero />
        <Earth />
        <Stages />
        <ReceiptDemo receipt={receipt} />
        <Rules />
        <Plans />
        <Faq />
        <Cta />
      </main>
      <Footer />
    </div>
  );
}

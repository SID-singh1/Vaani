import React, { useEffect, useState } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import { ArrowUpRight, Mic, Lock, Code } from 'lucide-react';
import './index.css';

const TELEGRAM = 'https://t.me/Vaani_hinglish_bot';
const GITHUB = 'https://github.com/SID-singh1/Vaani';
// Set VITE_APP_URL (e.g. in marketing/.env.production) to link the web app and privacy page.
const WEB_APP = import.meta.env.VITE_APP_URL;

const EXAMPLE_ACTIONS = [
  { task: 'Update the pricing deck', owner: 'Amit', due: 'tonight' },
  { task: 'Check the login bug on the demo environment', owner: 'Sneha', due: null },
  { task: 'Email the client the agenda', owner: 'You', due: 'tomorrow morning' },
];

function useFinePointer() {
  const [fine, setFine] = useState(() => window.matchMedia('(pointer: fine)').matches);
  useEffect(() => {
    const query = window.matchMedia('(pointer: fine)');
    const onChange = (e) => setFine(e.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);
  return fine;
}

function Cursor({ hovering }) {
  const [pos, setPos] = useState({ x: -100, y: -100 });
  useEffect(() => {
    const move = (e) => setPos({ x: e.clientX, y: e.clientY });
    window.addEventListener('mousemove', move);
    return () => window.removeEventListener('mousemove', move);
  }, []);
  return (
    <>
      <motion.div className="cursor-dot" animate={{ left: pos.x, top: pos.y }}
        transition={{ type: 'tween', ease: 'backOut', duration: 0.1 }} />
      <motion.div className="cursor-outline"
        animate={{ left: pos.x, top: pos.y, width: hovering ? 80 : 40, height: hovering ? 80 : 40,
          borderColor: hovering ? 'var(--neon-yellow)' : 'var(--neon-pink)' }}
        transition={{ type: 'tween', ease: 'backOut', duration: 0.15 }} />
    </>
  );
}

function Chips({ owner, due }) {
  return (
    <span className="chips">
      {owner && <span className="chip">👤 {owner}</span>}
      {due && <span className="chip due">⏰ {due}</span>}
    </span>
  );
}

function DemoCard() {
  return (
    <motion.div className="demo" initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.8, delay: 0.5 }}>
      <div className="bubble">
        <Mic size={18} /> <span className="wave" aria-hidden="true" /> <span>2:54</span>
        <p className="bubble-text">
          "Kal client ke saath call hai 4 baje. Amit tu pricing deck update kar dena by tonight,
          aur Sneha please demo pe login wala bug check kar lena…"
        </p>
      </div>
      <div className="arrow-down" aria-hidden="true">↓ ready in 9s</div>
      <div className="note-card">
        <div className="note-title">📝 Client call prep</div>
        <p className="note-summary">Client call tomorrow at 4 PM. The deck needs updated pricing and the demo's
          login bug must be checked before then.</p>
        <ul className="note-actions">
          {EXAMPLE_ACTIONS.map((a) => (
            <li key={a.task}><span className="check" aria-hidden="true" />{a.task} <Chips owner={a.owner} due={a.due} /></li>
          ))}
        </ul>
      </div>
    </motion.div>
  );
}

const FEATURES = [
  {
    n: '01', title: ['Minutes of audio,', 'seconds to read'], tone: 'cyan',
    desc: 'Forward a long voice note and get the gist before you could have played the first minute. A 3-minute note is usually ready in under 15 seconds.',
    visual: (
      <div className="vis-speed">
        <div className="speed-row"><span>🎧 Listening</span><span className="speed-bar slow" /><b>2m 54s</b></div>
        <div className="speed-row"><span>⚡ Vaani</span><span className="speed-bar fast" /><b>9s</b></div>
      </div>
    ),
  },
  {
    n: '02', title: ['Speaks', 'Hinglish'], tone: 'pink',
    desc: 'Talk the way you actually talk, switching between Hindi and English mid-sentence. You get a readable Romanized transcript and clean English action items with who and by when.',
    visual: (
      <div className="vis-hinglish">
        <p className="hinglish-in">"Amit tu pricing deck update kar dena by tonight"</p>
        <span className="arrow">→</span>
        <p className="hinglish-out">Update the pricing deck <Chips owner="Amit" due="tonight" /></p>
      </div>
    ),
  },
  {
    n: '03', title: ['Private', 'by design'], tone: 'yellow',
    desc: 'Audio is deleted as soon as it is transcribed, and you can wipe your notes any time. Want zero third parties? Vaani is open source: self-host it and run speech and AI fully on your own machine.',
    visual: (
      <div className="vis-private">
        <Lock size={64} />
        <p>Audio deleted after transcription</p>
        <p>/delete removes your notes for good</p>
        <p>Self-host: nothing leaves your machine</p>
      </div>
    ),
  },
];

const STEPS = [
  { title: 'Send', text: 'Record, forward a WhatsApp voice note, upload a file or paste text.' },
  { title: 'Vaani listens', text: 'It transcribes your Hindi, English or Hinglish and works out what matters.' },
  { title: 'You act', text: 'Summary, action items with owners and deadlines, and the full transcript.' },
];

export default function App() {
  const finePointer = useFinePointer();
  const [hovering, setHovering] = useState(false);
  const hover = finePointer ? { onMouseEnter: () => setHovering(true), onMouseLeave: () => setHovering(false) } : {};
  const { scrollYProgress } = useScroll();
  const heroOpacity = useTransform(scrollYProgress, [0, 0.18], [1, 0.2]);

  return (
    <div className={finePointer ? 'custom-cursor' : ''}>
      {finePointer && <Cursor hovering={hovering} />}
      <div className="bg-blob blob-1" />
      <div className="bg-blob blob-2" />

      <div className="container">
        <motion.section className="hero" style={{ opacity: heroOpacity }}>
          <div className="hero-copy">
            <motion.h1 className="hero-title" initial={{ y: 80, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
              transition={{ duration: 1, type: 'spring', bounce: 0.4 }}>
              <span className="glitch" data-text="Voice notes,">Voice notes,</span><br />
              <span className="text-stroke">sorted.</span>
            </motion.h1>
            <motion.p className="hero-subtitle" initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
              transition={{ duration: 1, delay: 0.2 }}>
              Send a voice note in Hindi, English or Hinglish. Get a summary, who-does-what action items
              and a clean transcript in seconds.
            </motion.p>
            <motion.div className="cta-row" initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.5, delay: 0.4 }}>
              <a href={TELEGRAM} target="_blank" rel="noopener noreferrer" className="cyber-button" {...hover}>
                Try on Telegram <ArrowUpRight />
              </a>
              {WEB_APP && (
                <a href={WEB_APP} target="_blank" rel="noopener noreferrer" className="cyber-button ghost" {...hover}>
                  Open web app <ArrowUpRight />
                </a>
              )}
            </motion.div>
            <p className="hero-note">Free · no sign-up · WhatsApp coming soon</p>
          </div>
          <DemoCard />
        </motion.section>

        <section className="features-section">
          <div className="marquee-container" aria-hidden="true">
            <motion.div className="marquee-text" animate={{ x: [0, -1000] }}
              transition={{ x: { repeat: Infinity, repeatType: 'loop', duration: 12, ease: 'linear' } }}>
              HINGLISH · SUMMARIES · ACTION ITEMS · TELEGRAM · WEB · HINGLISH · SUMMARIES · ACTION ITEMS ·
            </motion.div>
          </div>

          <div className="feature-grid">
            {FEATURES.map((f, i) => (
              <motion.div key={f.n} className={`feature-row${i % 2 ? ' reverse' : ''}`}
                initial={{ opacity: 0, y: 80 }} whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-15%' }} transition={{ duration: 0.8, type: 'spring', bounce: 0.3 }}>
                <div className="feature-content">
                  <div className="feature-number">{f.n}</div>
                  <h2 className="feature-title">{f.title[0]}<br />{f.title[1]}</h2>
                  <p className="feature-desc">{f.desc}</p>
                </div>
                <div className={`feature-visual tone-${f.tone}`} {...hover}>{f.visual}</div>
              </motion.div>
            ))}
          </div>
        </section>

        <section className="steps-section">
          <h2 className="section-title">How it works</h2>
          <div className="steps">
            {STEPS.map((s, i) => (
              <motion.div key={s.title} className="step" initial={{ opacity: 0, y: 40 }}
                whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: i * 0.15 }}>
                <span className="step-n">{i + 1}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </motion.div>
            ))}
          </div>
          <div className="cta-row center">
            <a href={TELEGRAM} target="_blank" rel="noopener noreferrer" className="cyber-button" {...hover}>
              Send your first note <ArrowUpRight />
            </a>
          </div>
        </section>

        <footer>
          <h2 className="footer-logo">VAANI</h2>
          <nav className="footer-links">
            <a href={TELEGRAM} target="_blank" rel="noopener noreferrer">Telegram</a>
            {WEB_APP && <a href={WEB_APP} target="_blank" rel="noopener noreferrer">Web app</a>}
            {WEB_APP && <a href={`${WEB_APP.replace(/\/$/, '')}/privacy`} target="_blank" rel="noopener noreferrer">Privacy</a>}
            <a href={GITHUB} target="_blank" rel="noopener noreferrer"><Code size={16} /> Open source</a>
          </nav>
          <p>Made in India for the way India talks.</p>
        </footer>
      </div>
    </div>
  );
}

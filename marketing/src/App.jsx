import React, { useEffect, useState } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import { Bot, ShieldAlert, Zap, ArrowUpRight } from 'lucide-react';
import './index.css';

function App() {
  const telegramLink = "https://t.me/Vaani_hinglish_bot";
  // Set VITE_APP_URL (e.g. in marketing/.env.production) to show the "Open web app" button.
  const webAppLink = import.meta.env.VITE_APP_URL;
  
  // Custom Cursor Logic
  const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });
  const [isHovering, setIsHovering] = useState(false);
  
  useEffect(() => {
    const updateMousePosition = (e) => {
      setMousePosition({ x: e.clientX, y: e.clientY });
    };
    window.addEventListener('mousemove', updateMousePosition);
    return () => window.removeEventListener('mousemove', updateMousePosition);
  }, []);

  // Scroll Animations
  const { scrollYProgress } = useScroll();
  const yBg = useTransform(scrollYProgress, [0, 1], ["0%", "50%"]);
  const opacityHero = useTransform(scrollYProgress, [0, 0.2], [1, 0]);
  const scaleHero = useTransform(scrollYProgress, [0, 0.2], [1, 0.8]);

  // Marquee Animation
  const marqueeVariants = {
    animate: {
      x: [0, -1000],
      transition: { x: { repeat: Infinity, repeatType: "loop", duration: 10, ease: "linear" } }
    }
  };

  return (
    <>
      {/* Custom Cursor */}
      <motion.div 
        className="cursor-dot" 
        animate={{ left: mousePosition.x, top: mousePosition.y }}
        transition={{ type: "tween", ease: "backOut", duration: 0.1 }}
      />
      <motion.div 
        className="cursor-outline" 
        animate={{ 
          left: mousePosition.x, 
          top: mousePosition.y,
          width: isHovering ? 80 : 40,
          height: isHovering ? 80 : 40,
          borderColor: isHovering ? 'var(--neon-yellow)' : 'var(--neon-pink)'
        }}
        transition={{ type: "tween", ease: "backOut", duration: 0.15 }}
      />

      {/* Animated Background Blobs */}
      <div className="bg-blob blob-1"></div>
      <div className="bg-blob blob-2"></div>
      
      <div className="container">
        {/* Hero Section */}
        <motion.section 
          className="hero"
          style={{ opacity: opacityHero, scale: scaleHero, y: yBg }}
        >
          <motion.h1 
            className="hero-title"
            initial={{ y: 100, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 1, type: "spring", bounce: 0.4 }}
          >
            <motion.div
              animate={{ y: [0, -10, 0] }}
              transition={{ repeat: Infinity, duration: 4, ease: "easeInOut" }}
            >
              <span className="glitch" data-text="Voice AI">Voice AI</span> <br/> 
              <span className="text-stroke">Reimagined.</span>
            </motion.div>
          </motion.h1>
          
          <motion.p 
            className="hero-subtitle"
            initial={{ y: 50, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 1, delay: 0.2 }}
          >
            Send a Hindi, English or Hinglish voice note. Get a summary, who-does-what action items
            and a clean Hinglish transcript in seconds.
          </motion.p>
          
          <motion.div
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.4 }}
          >
            <a 
              href={telegramLink} 
              target="_blank" 
              rel="noopener noreferrer"
              className="cyber-button"
              onMouseEnter={() => setIsHovering(true)}
              onMouseLeave={() => setIsHovering(false)}
            >
              Try on Telegram <ArrowUpRight />
            </a>
            {webAppLink && (
              <a
                href={webAppLink}
                target="_blank"
                rel="noopener noreferrer"
                className="cyber-button"
                style={{ marginLeft: '1rem' }}
                onMouseEnter={() => setIsHovering(true)}
                onMouseLeave={() => setIsHovering(false)}
              >
                Open web app <ArrowUpRight />
              </a>
            )}
          </motion.div>
        </motion.section>

        {/* Features Section */}
        <section className="features-section">
          {/* Marquee */}
          <div className="marquee-container">
            <motion.div className="marquee-text" variants={marqueeVariants} animate="animate">
              INSTANT HINGLISH RECOGNITION • INTELLIGENT SUMMARIES • SEAMLESS TELEGRAM INTEGRATION • INSTANT HINGLISH RECOGNITION • INTELLIGENT SUMMARIES • SEAMLESS TELEGRAM INTEGRATION • 
            </motion.div>
          </div>

          <div className="feature-grid">
            {/* Feature 1 */}
            <motion.div 
              className="feature-row"
              initial={{ opacity: 0, x: -100, rotateY: 45, scale: 0.8 }}
              whileInView={{ opacity: 1, x: 0, rotateY: 0, scale: 1 }}
              viewport={{ once: false, margin: "-20%" }}
              transition={{ duration: 1, type: "spring", bounce: 0.5 }}
            >
              <div className="feature-content">
                <div className="feature-number">01</div>
                <h2 className="feature-title">Fast <br/> Mode</h2>
                <p className="feature-desc">Whisper large-v3 on Groq plus Gemini, with automatic failover between models. A 3-minute voice note comes back in about 13 seconds.</p>
              </div>
              <div className="feature-visual" onMouseEnter={() => setIsHovering(true)} onMouseLeave={() => setIsHovering(false)}>
                <ShieldAlert className="visual-icon pink" />
              </div>
            </motion.div>

            {/* Feature 2 */}
            <motion.div 
              className="feature-row reverse"
              initial={{ opacity: 0, x: 100, rotateX: 45, scale: 0.8 }}
              whileInView={{ opacity: 1, x: 0, rotateX: 0, scale: 1 }}
              viewport={{ once: false, margin: "-20%" }}
              transition={{ duration: 1, type: "spring", bounce: 0.5 }}
            >
              <div className="feature-content">
                <div className="feature-number">02</div>
                <h2 className="feature-title">Built for <br/> Hinglish</h2>
                <p className="feature-desc">Code-switched speech becomes a readable Romanized Hinglish transcript, and action items come out in English with the owner and the deadline.</p>
              </div>
              <div className="feature-visual" onMouseEnter={() => setIsHovering(true)} onMouseLeave={() => setIsHovering(false)}>
                <Bot className="visual-icon cyan" />
              </div>
            </motion.div>

            {/* Feature 3 */}
            <motion.div 
              className="feature-row"
              initial={{ opacity: 0, y: 150, rotateZ: -10, scale: 0.8 }}
              whileInView={{ opacity: 1, y: 0, rotateZ: 0, scale: 1 }}
              viewport={{ once: false, margin: "-20%" }}
              transition={{ duration: 1, type: "spring", bounce: 0.5 }}
            >
              <div className="feature-content">
                <div className="feature-number">03</div>
                <h2 className="feature-title">Private <br/> Mode</h2>
                <p className="feature-desc">Open source and self-hostable: a quantized Whisper and a local LLM run entirely on your own machine, so audio never reaches an AI company.</p>
              </div>
              <div className="feature-visual" onMouseEnter={() => setIsHovering(true)} onMouseLeave={() => setIsHovering(false)}>
                <Zap className="visual-icon" />
              </div>
            </motion.div>
          </div>
        </section>

        {/* Footer */}
        <footer>
          <h2 className="footer-logo">VAANI</h2>
          <p>Engineered for the future.</p>
        </footer>
      </div>
    </>
  );
}

export default App;

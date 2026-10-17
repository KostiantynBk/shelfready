"use client";

/* eslint-disable @next/next/no-img-element -- supplier photos served by the API in many sizes */
import { use, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, fileUrl, LANGUAGE_NAMES, type Product } from "@/lib/api";

type Message = { from: "shopper" | "shop"; text: string };
type AskReply = { answer: string; conversation_id: string | null };

const photoRank = (role?: string) => (role === "packshot" ? 0 : role === "detail" ? 2 : 1);

export function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [product, setProduct] = useState<Product | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lang, setLang] = useState("en");
  const [photo, setPhoto] = useState<string | null>(null);

  useEffect(() => {
    api<Product>(`/api/products/${encodeURIComponent(id)}`)
      .then(setProduct)
      .catch((e: unknown) =>
        setLoadError(
          e instanceof ApiError && e.status === 404
            ? "We could not find this product."
            : "This page could not be loaded. Please try again later.",
        ),
      );
  }, [id]);

  const copy = product?.copy ?? null;
  const languages = copy ? ["en", ...Object.keys(copy.translations)] : [];
  const shown = copy ? (lang === "en" ? copy.en : (copy.translations[lang]?.copy ?? copy.en)) : null;
  const photos =
    product && copy
      ? copy.photos
          .flatMap((p) => {
            const file = product.files.find((f) => f.name === p.file);
            return file
              ? [{ name: file.name, url: fileUrl(file.url), thumb: fileUrl(file.thumb_url ?? file.url), role: file.analysis?.role }]
              : [];
          })
          .sort((a, b) => photoRank(a.role) - photoRank(b.role))
      : [];
  const main = photos.find((p) => p.name === photo) ?? photos[0];
  const altFor = (file: string) => shown?.alt_texts.find((a) => a.file === file)?.text ?? shown?.title ?? "";

  return (
    <div className="shop">
      <header className="shop-head">
        <div className="shop-bar">
          <span className="shop-logo">Casa Nord</span>
          {languages.length > 1 && (
            <label className="shop-lang">
              Language
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {languages.map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_NAMES[code] ?? code.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </header>

      <main className="shop-main">
        {loadError ? (
          <p className="shop-empty">{loadError}</p>
        ) : !product ? (
          <p className="shop-empty">Loading…</p>
        ) : !shown ? (
          <p className="shop-empty">This product is not listed yet.</p>
        ) : (
          <>
            {product.status !== "published" && <p className="shop-preview">Preview: not published yet</p>}
            <div className="shop-product">
              <div className="shop-gallery" lang={lang}>
                <div className="shop-photo">
                  {main ? <img src={main.url} alt={altFor(main.name)} /> : <span className="muted">No photos yet</span>}
                </div>
                {photos.length > 1 && (
                  <div className="shop-thumbs">
                    {photos.map((p) => (
                      <button
                        key={p.name}
                        type="button"
                        className={p.name === main?.name ? "on" : undefined}
                        aria-pressed={p.name === main?.name}
                        onClick={() => setPhoto(p.name)}
                      >
                        <img src={p.thumb} alt={altFor(p.name)} />
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="shop-info">
                <div lang={lang}>
                  <p className="shop-vendor">{product.supplier}</p>
                  <h1>{shown.title}</h1>
                  {shown.colours.length > 0 && (
                    <>
                      <p className="shop-label">Colours</p>
                      <ul className="shop-colours">
                        {shown.colours.map((c) => (
                          <li key={c.code}>{c.name}</li>
                        ))}
                      </ul>
                    </>
                  )}
                  <ul className="shop-bullets">
                    {shown.bullets.map((b) => (
                      <li key={b}>{b}</li>
                    ))}
                  </ul>
                  <p className="shop-desc">{shown.description}</p>
                  {shown.safety_warnings.length > 0 && (
                    <section className="shop-safety">
                      <h2>Safety information</h2>
                      <ul>
                        {shown.safety_warnings.map((w) => (
                          <li key={w}>{w}</li>
                        ))}
                      </ul>
                    </section>
                  )}
                </div>
                <AskBox id={id} language={lang} />
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function askProblem(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503 && typeof e.detail === "string") return e.detail;
    if (e.status === 404 || e.status === 503) return "The shop assistant is not available right now.";
    if (typeof e.detail === "string") return e.detail;
  }
  return "Your question could not be sent. Please try again.";
}

function AskBox({ id, language }: { id: string; language: string }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const chat = chatRef.current;
    if (chat) chat.scrollTop = chat.scrollHeight;
  }, [messages, asking]);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (!text || asking) return;
    setMessages((m) => [...m, { from: "shopper", text }]);
    setQuestion("");
    setProblem(null);
    setAsking(true);
    try {
      const reply = await api<AskReply>(`/api/shop/${encodeURIComponent(id)}/ask`, {
        method: "POST",
        body: JSON.stringify({ question: text, conversation_id: conversationId, language }),
      });
      setMessages((m) => [...m, { from: "shop", text: reply.answer }]);
      setConversationId(reply.conversation_id);
    } catch (e) {
      setProblem(askProblem(e));
    } finally {
      setAsking(false);
    }
  }

  return (
    <>
      <section className="shop-ask" aria-labelledby="shop-ask-title">
        <h2 id="shop-ask-title">Questions about this product?</h2>
        {(messages.length > 0 || asking) && (
          <div className="shop-chat" ref={chatRef} aria-live="polite">
            {messages.map((m, i) => (
              <p key={i} className={m.from === "shopper" ? "shop-bubble q" : "shop-bubble a"}>
                {m.text}
              </p>
            ))}
            {asking && <p className="shop-bubble a muted">Looking it up…</p>}
          </div>
        )}
        {problem && <p className="shop-problem">{problem}</p>}
        <form className="shop-askform" onSubmit={ask}>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about size, materials, care…"
            aria-label="Your question"
            maxLength={500}
          />
          <button type="submit" disabled={asking || !question.trim()}>
            Ask
          </button>
        </form>
      </section>
      <p className="shop-note">
        Answers come only from this shop&apos;s verified product facts (Evolus agent on Gemma 4 31B, AMD).
      </p>
    </>
  );
}

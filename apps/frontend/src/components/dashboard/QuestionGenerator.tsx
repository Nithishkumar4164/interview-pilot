/**
 * Generate the next question during the interview.
 *
 * Pick a category, take a suggested topic (or type one), and generate a
 * question with its hints, approaches and pitfalls. Regenerate a draft that
 * won't work, optionally saying what should change; the rejected drafts go
 * back so the next one differs. "Use this question" logs it to the
 * questionnaire and makes it the current question.
 */
import { useCallback, useEffect, useState } from 'react';
import * as api from '@/api/client';
import { ApproachBlock } from './ApproachBlock';

export const QUESTION_CATEGORIES = [
  'coding',
  'dsa',
  'debugging',
  'system_design',
  'api_design',
  'behavioral',
  'aptitude',
] as const;

const HINT_LABELS: Record<number, string> = { 1: 'Nudge', 2: 'Guide', 3: 'Direct' };

export function categoryLabel(category: string): string {
  return category.replace('_', ' ');
}

type TopicsState = { status: 'loading' } | { status: 'ready'; topics: api.TopicSuggestion[] } | { status: 'failed' };

interface QuestionGeneratorProps {
  sessionId: string;
  /** The session's focus areas, offered first. */
  focusAreas: string[];
  onUse: (category: string, topic: string | null, question: api.GeneratedQuestion) => Promise<void>;
}

export function QuestionGenerator({ sessionId, focusAreas, onUse }: QuestionGeneratorProps) {
  const [category, setCategory] = useState<string>(focusAreas[0] ?? 'coding');
  const [topics, setTopics] = useState<Record<string, TopicsState>>({});
  const [topic, setTopic] = useState('');
  const [draft, setDraft] = useState<api.GeneratedQuestion | null>(null);
  const [rejected, setRejected] = useState<string[]>([]);
  const [feedback, setFeedback] = useState('');
  const [generating, setGenerating] = useState(false);
  const [using, setUsing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Start on the first focus area once the session's focus areas arrive.
  useEffect(() => {
    if (focusAreas[0]) setCategory((c) => (focusAreas.includes(c) ? c : focusAreas[0]));
  }, [focusAreas]);

  const loadTopics = useCallback(
    async (cat: string) => {
      setTopics((t) => ({ ...t, [cat]: { status: 'loading' } }));
      try {
        const res = await api.suggestTopics(sessionId, cat);
        setTopics((t) => ({ ...t, [cat]: { status: 'ready', topics: res.topics } }));
      } catch {
        setTopics((t) => ({ ...t, [cat]: { status: 'failed' } }));
      }
    },
    [sessionId],
  );

  // Suggest topics for a category the first time it is chosen.
  useEffect(() => {
    if (!topics[category]) void loadTopics(category);
  }, [category, topics, loadTopics]);

  /** A new slot: drafts rejected for another topic say nothing about this one. */
  const startOver = useCallback(() => {
    setDraft(null);
    setRejected([]);
    setFeedback('');
    setError(null);
  }, []);

  const generate = useCallback(
    async (rejectedDrafts: string[]) => {
      setGenerating(true);
      setError(null);
      try {
        const res = await api.generateQuestion(sessionId, {
          category,
          topic: topic.trim() || null,
          rejected_drafts: rejectedDrafts,
          feedback: feedback.trim() || null,
        });
        setDraft(res.question);
        setRejected(rejectedDrafts);
        setFeedback('');
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't generate a question");
      } finally {
        setGenerating(false);
      }
    },
    [sessionId, category, topic, feedback],
  );

  const handleUse = useCallback(async () => {
    if (!draft) return;
    setUsing(true);
    setError(null);
    try {
      await onUse(category, topic.trim() || null, draft);
      startOver();
      setTopic('');
      // What was asked changed: the next suggestions for this category must avoid it.
      setTopics((t) => {
        const next = { ...t };
        delete next[category];
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't use this question");
    } finally {
      setUsing(false);
    }
  }, [draft, category, topic, onUse, startOver]);

  const others = QUESTION_CATEGORIES.filter((c) => !focusAreas.includes(c));
  const topicsState = topics[category];

  return (
    <div className="question-generator" aria-label="New question">
      <div className="form-group">
        <label htmlFor="question-category">Category</label>
        <select
          id="question-category"
          value={category}
          onChange={(e) => {
            setCategory(e.target.value);
            setTopic('');
            startOver();
          }}
        >
          {focusAreas.length > 0 && (
            <optgroup label="Focus areas">
              {focusAreas.map((c) => (
                <option key={c} value={c}>
                  {categoryLabel(c)}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label={focusAreas.length > 0 ? 'Other' : 'Categories'}>
            {others.map((c) => (
              <option key={c} value={c}>
                {categoryLabel(c)}
              </option>
            ))}
          </optgroup>
        </select>
      </div>

      <div className="form-group">
        <div className="question-generator__topics-header">
          <label htmlFor="question-topic">Topic</label>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void loadTopics(category)}
            disabled={topicsState?.status === 'loading'}
          >
            More suggestions
          </button>
        </div>
        <div className="question-generator__suggestions" role="group" aria-label="Suggested topics">
          {topicsState?.status === 'loading' && <span className="form-hint">Suggesting topics…</span>}
          {topicsState?.status === 'failed' && (
            <span className="form-hint">Couldn't suggest topics. Type one, or generate without a topic.</span>
          )}
          {topicsState?.status === 'ready' &&
            topicsState.topics.map((t) => {
              const value = t.concept ? `${t.concept}: ${t.topic}` : t.topic;
              return (
                <button
                  key={value}
                  type="button"
                  className={`chip question-generator__topic ${topic === value ? 'chip--active' : ''}`}
                  title={t.topic}
                  onClick={() => {
                    setTopic(value);
                    startOver();
                  }}
                >
                  {t.concept || t.topic}
                </button>
              );
            })}
        </div>
        <input
          id="question-topic"
          type="text"
          placeholder="Pick a suggestion or type a topic (optional)"
          value={topic}
          onChange={(e) => {
            setTopic(e.target.value);
            if (draft) startOver();
          }}
        />
      </div>

      {!draft && (
        <button type="button" className="btn btn--primary" onClick={() => void generate([])} disabled={generating}>
          {generating ? 'Generating…' : 'Generate question'}
        </button>
      )}

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      {draft && <DraftCard draft={draft} />}

      {draft && (
        <div className="question-generator__actions">
          <div className="form-group">
            <input
              type="text"
              aria-label="What should change?"
              placeholder="What should change? (optional, e.g. harder, less about trees)"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
          </div>
          <div className="question-generator__buttons">
            <button
              type="button"
              className="btn btn--secondary btn--sm"
              onClick={() => void generate([...rejected, draft.text])}
              disabled={generating || using}
            >
              {generating ? 'Regenerating…' : 'Regenerate'}
            </button>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={() => void handleUse()}
              disabled={generating || using}
            >
              {using ? 'Adding…' : 'Use this question'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function DraftCard({ draft }: { draft: api.GeneratedQuestion }) {
  return (
    <article className="question-generator__draft" aria-label="Draft question">
      <p className="current-question-card__text">{draft.text}</p>
      <div className="current-question-card__meta">
        <span className={`phase-section__badge phase-section__badge--${draft.category}`}>
          {categoryLabel(draft.category)}
        </span>
        <span className="question-card__difficulty" aria-label={`Difficulty ${draft.difficulty} of 5`}>
          {Array.from({ length: 5 }, (_, i) => (
            <span
              key={i}
              className={`question-card__difficulty-dot ${i < draft.difficulty ? 'question-card__difficulty-dot--filled' : ''}`}
            />
          ))}
        </span>
        <span className="question-card__time">{draft.time_minutes} min</span>
      </div>

      {draft.hints.length > 0 && (
        <details className="question-generator__section">
          <summary>Hints ({draft.hints.length})</summary>
          <ol className="question-generator__hints">
            {draft.hints.map((h) => (
              <li key={h.level}>
                <strong>{HINT_LABELS[h.level] ?? `Level ${h.level}`}:</strong> {h.text}
              </li>
            ))}
          </ol>
        </details>
      )}
      {(draft.brute_force_approach || (draft.optimal_approaches?.length ?? 0) > 0) && (
        <details className="question-generator__section">
          <summary>Approaches</summary>
          {draft.brute_force_approach && <ApproachBlock approach={draft.brute_force_approach} />}
          {draft.optimal_approaches?.map((a, i) => <ApproachBlock key={i} approach={a} />)}
        </details>
      )}
      {(draft.common_pitfalls?.length ?? 0) > 0 && (
        <details className="question-generator__section">
          <summary>Common pitfalls ({draft.common_pitfalls!.length})</summary>
          <ul className="plan-panel__pitfalls">
            {draft.common_pitfalls!.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

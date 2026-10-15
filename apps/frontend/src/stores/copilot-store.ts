/**
 * Copilot store — what the peer interviewer whispers to the interviewer.
 *
 * Cards arrive on the `copilot_card` channel (probes, claim checks,
 * clarifications, coaching, pacing, stuck, difficulty, edge cases). The
 * interviewer marks a card as used (asked the probe, relayed the reply) or
 * dismisses it; either way it leaves the list. Kinds can be muted; the mute
 * list is a per-viewer convenience kept in localStorage.
 */
import { create } from 'zustand';

export type CopilotCardKind =
  | 'probe'
  | 'claim_check'
  | 'clarification'
  | 'coaching'
  | 'pace'
  | 'stuck'
  | 'difficulty'
  | 'edge_cases';

export type CopilotPriority = 'info' | 'suggest' | 'urgent';

export interface CopilotCard {
  id: string;
  kind: CopilotCardKind;
  priority: CopilotPriority;
  title: string;
  body: string;
  items: string[];
  /** What the interviewer can say to the candidate, when the card has one thing to relay. */
  probe: string;
  questionId: string;
  source: 'llm' | 'rule';
  timestamp: string;
}

export const CARD_KIND_LABELS: Record<CopilotCardKind, string> = {
  probe: 'Follow-up',
  claim_check: 'Claim check',
  clarification: 'Clarify',
  coaching: 'Coaching',
  pace: 'Pacing',
  stuck: 'Stuck',
  difficulty: 'Difficulty',
  edge_cases: 'Edge cases',
};

export const ALL_CARD_KINDS = Object.keys(CARD_KIND_LABELS) as CopilotCardKind[];

const MAX_CARDS = 20;
const MUTE_KEY = 'copilot_muted_kinds';

function loadMuted(): Set<CopilotCardKind> {
  try {
    const raw = localStorage.getItem(MUTE_KEY);
    const parsed = raw ? (JSON.parse(raw) as string[]) : [];
    return new Set(parsed.filter((k): k is CopilotCardKind => k in CARD_KIND_LABELS));
  } catch {
    return new Set();
  }
}

function saveMuted(muted: Set<CopilotCardKind>): void {
  try {
    localStorage.setItem(MUTE_KEY, JSON.stringify([...muted]));
  } catch {
    // per-viewer convenience only
  }
}

export function cardFromPayload(p: Record<string, unknown>, fallbackTimestamp: string): CopilotCard | null {
  const id = typeof p.id === 'string' ? p.id : '';
  const kind = typeof p.kind === 'string' ? p.kind : '';
  if (!id || !(kind in CARD_KIND_LABELS)) return null;
  const items = Array.isArray(p.items) ? p.items.filter((x): x is string => typeof x === 'string') : [];
  return {
    id,
    kind: kind as CopilotCardKind,
    priority: p.priority === 'urgent' || p.priority === 'info' ? p.priority : 'suggest',
    title: typeof p.title === 'string' ? p.title : CARD_KIND_LABELS[kind as CopilotCardKind],
    body: typeof p.body === 'string' ? p.body : '',
    items,
    probe: typeof p.probe === 'string' ? p.probe : '',
    questionId: typeof p.question_id === 'string' ? p.question_id : '',
    source: p.source === 'rule' ? 'rule' : 'llm',
    timestamp: typeof p.timestamp === 'string' ? p.timestamp : fallbackTimestamp,
  };
}

interface CopilotState {
  cards: CopilotCard[]; // newest first
  usedIds: string[];
  mutedKinds: Set<CopilotCardKind>;

  addCard: (card: CopilotCard) => void;
  dismissCard: (id: string) => void;
  markUsed: (id: string) => void;
  toggleMute: (kind: CopilotCardKind) => void;
  clear: () => void;
}

export const useCopilotStore = create<CopilotState>((set) => ({
  cards: [],
  usedIds: [],
  mutedKinds: loadMuted(),

  addCard: (card) =>
    set((s) => {
      // One card per id (a resumed run can re-send the same card); used cards stay gone.
      if (s.usedIds.includes(card.id)) return s;
      return { cards: [card, ...s.cards.filter((c) => c.id !== card.id)].slice(0, MAX_CARDS) };
    }),

  dismissCard: (id) => set((s) => ({ cards: s.cards.filter((c) => c.id !== id) })),

  markUsed: (id) =>
    set((s) => ({
      cards: s.cards.filter((c) => c.id !== id),
      usedIds: [...s.usedIds, id].slice(-100),
    })),

  toggleMute: (kind) =>
    set((s) => {
      const muted = new Set(s.mutedKinds);
      if (muted.has(kind)) muted.delete(kind);
      else muted.add(kind);
      saveMuted(muted);
      return { mutedKinds: muted };
    }),

  clear: () => set({ cards: [], usedIds: [] }),
}));

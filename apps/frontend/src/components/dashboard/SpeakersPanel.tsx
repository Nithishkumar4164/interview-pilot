/**
 * Speakers panel — manages Root Speakers and Speech Vectors.
 *
 * Displays a collapsible list of root speakers, each showing
 * child speech vectors with reassignment controls.
 */
import { useState } from 'react';
import { useSpeakerStore, type RootSpeaker, type SpeechVector } from '@/stores/speaker-store';
import { useWSSend } from '@/contexts/WebSocketContext';
import { PanelTitle } from './PanelTitle';
import { usePaneHidden } from '@/stores/panel-store';

export function SpeakersPanel() {
  const hidden = usePaneHidden('speakers');
  const rootSpeakers = useSpeakerStore((s) => s.rootSpeakers);
  const speechVectors = useSpeakerStore((s) => s.speechVectors);
  const send = useWSSend();

  const [expandedRs, setExpandedRs] = useState<Record<string, boolean>>({});
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newLabel, setNewLabel] = useState('');
  const [newColor, setNewColor] = useState('#9399b0');
  const [editingRs, setEditingRs] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState('');

  const rsEntries = Object.values(rootSpeakers);

  const toggleExpand = (rsId: string) => {
    setExpandedRs((prev) => ({ ...prev, [rsId]: !prev[rsId] }));
  };

  const handleCreate = () => {
    if (!newLabel.trim()) return;
    send({ type: 'create_root_speaker', label: newLabel.trim(), color: newColor });
    setNewLabel('');
    setNewColor('#9399b0');
    setShowCreateForm(false);
  };

  const handleRename = (rsId: string) => {
    if (!editLabel.trim()) return;
    send({ type: 'rename_root_speaker', rs_id: rsId, label: editLabel.trim() });
    setEditingRs(null);
    setEditLabel('');
  };

  const handleDelete = (rsId: string) => {
    send({ type: 'delete_root_speaker', rs_id: rsId });
  };

  const handleReassignVector = (svId: string, targetRsId: string) => {
    send({ type: 'reassign_speech_vector', sv_id: svId, target_rs_id: targetRsId });
  };

  const getChildVectors = (rs: RootSpeaker): SpeechVector[] => {
    return rs.speech_vector_ids
      .map((svId) => speechVectors[svId])
      .filter(Boolean);
  };

  const channelBadge = (channel: string) => {
    if (channel === 'mic') return 'MIC';
    if (channel === 'tab') return 'TAB';
    if (channel === 'virtual') return 'VIRT';
    return channel.toUpperCase();
  };

  return (
    <section className="speakers-panel" hidden={hidden} aria-label="Speakers">
      <div className="speakers-panel__header panel-header">
        <PanelTitle id="speakers">Speakers</PanelTitle>
        <button
          className="btn btn--small btn--ghost"
          onClick={() => setShowCreateForm(!showCreateForm)}
        >
          {showCreateForm ? 'Cancel' : '+ Add'}
        </button>
      </div>

      {showCreateForm && (
        <div className="create-speaker-form">
          <input
            className="create-speaker-form__input"
            type="text"
            placeholder="Speaker name"
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
          />
          <input
            className="create-speaker-form__color"
            type="color"
            value={newColor}
            onChange={(e) => setNewColor(e.target.value)}
          />
          <button className="btn btn--small btn--primary" onClick={handleCreate}>
            Add
          </button>
        </div>
      )}

      <div className="speakers-panel__list">
        {rsEntries.map((rs) => {
          const expanded = expandedRs[rs.id] ?? false;
          const childVectors = getChildVectors(rs);

          return (
            <div key={rs.id} className="root-speaker-row">
              <div
                className="root-speaker-row__header"
                onClick={() => toggleExpand(rs.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && toggleExpand(rs.id)}
              >
                <span
                  className="root-speaker-row__dot"
                  style={{ backgroundColor: rs.color }}
                />
                {editingRs === rs.id ? (
                  <input
                    className="root-speaker-row__edit-input"
                    type="text"
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    onKeyDown={(e) => {
                      e.stopPropagation();
                      if (e.key === 'Enter') handleRename(rs.id);
                      if (e.key === 'Escape') setEditingRs(null);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                  />
                ) : (
                  <span
                    className="root-speaker-row__label"
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      setEditingRs(rs.id);
                      setEditLabel(rs.label);
                    }}
                  >
                    {rs.label}
                  </span>
                )}
                {!rs.is_default && (
                  <button
                    className="root-speaker-row__delete btn btn--small btn--ghost"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(rs.id);
                    }}
                    title="Delete speaker"
                  >
                    x
                  </button>
                )}
                <span className="root-speaker-row__chevron">
                  {expanded ? '\u25BC' : '\u25B6'}
                </span>
              </div>

              {expanded && childVectors.length > 0 && (
                <div className="root-speaker-row__children">
                  {childVectors.map((sv) => (
                    <SpeechVectorRow
                      key={sv.id}
                      sv={sv}
                      rsEntries={rsEntries}
                      channelBadge={channelBadge}
                      onReassign={handleReassignVector}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function SpeechVectorRow({
  sv,
  rsEntries,
  channelBadge,
  onReassign,
}: {
  sv: SpeechVector;
  rsEntries: RootSpeaker[];
  channelBadge: (channel: string) => string;
  onReassign: (svId: string, targetRsId: string) => void;
}) {
  const [showTooltip, setShowTooltip] = useState(false);
  const send = useWSSend();
  const cachedTranscripts = useSpeakerStore((s) => s.svTranscripts[sv.id]);

  const handleMouseEnter = () => {
    // Ask the backend for the latest transcripts for this speech vector
    send({ type: 'get_sv_transcripts', sv_id: sv.id });
    setShowTooltip(true);
  };

  const handleMouseLeave = () => {
    setShowTooltip(false);
  };

  return (
    <div className="speech-vector-row">
      <span className={`speech-vector-row__badge speech-vector-row__badge--${sv.channel}`}>
        {channelBadge(sv.channel)}
      </span>
      <span className="speech-vector-row__id">{sv.id}</span>

      {sv.transcript_ids.length > 0 && (
        <span
          className="speech-vector-row__transcript-btn"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          aria-label="Recent transcripts"
        >
          {/* transcript icon (SVG notepad) */}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <line x1="10" y1="9" x2="8" y2="9" />
          </svg>

          {showTooltip && (
            <div className="speech-vector-row__tooltip">
              {cachedTranscripts && cachedTranscripts.length > 0 ? (
                cachedTranscripts.map((t) => (
                  <p key={t.id} className="speech-vector-row__tooltip-line">
                    {t.text}
                  </p>
                ))
              ) : (
                <p className="speech-vector-row__tooltip-line speech-vector-row__tooltip-line--empty">
                  Loading...
                </p>
              )}
            </div>
          )}
        </span>
      )}

      <select
        className="speech-vector-row__reassign"
        value={sv.root_speaker_id}
        onChange={(e) => onReassign(sv.id, e.target.value)}
        onClick={(e) => e.stopPropagation()}
      >
        {rsEntries.map((opt) => (
          <option key={opt.id} value={opt.id}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}

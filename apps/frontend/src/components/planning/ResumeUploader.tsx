/**
 * Drag-and-drop resume uploader with SSE-based extraction status.
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { usePlanStore } from '@/stores/plan-store';
import * as api from '@/api/client';

interface ResumeUploaderProps {
  onUploadComplete: (referenceId: string, data: Record<string, unknown>) => void;
}

const ALLOWED_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
];
const MAX_SIZE = 10 * 1024 * 1024; // 10 MB

export function ResumeUploader({ onUploadComplete }: ResumeUploaderProps) {
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const resume = usePlanStore((s) => s.resume);
  const setResumeUploading = usePlanStore((s) => s.setResumeUploading);
  const setResumeProcessing = usePlanStore((s) => s.setResumeProcessing);
  const setResumeCompleted = usePlanStore((s) => s.setResumeCompleted);
  const setResumeFailed = usePlanStore((s) => s.setResumeFailed);
  const clearResume = usePlanStore((s) => s.clearResume);

  // Close EventSource on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  const handleFile = useCallback(
    async (file: File) => {
      if (!ALLOWED_TYPES.includes(file.type)) {
        setResumeFailed('Unsupported file type. Please upload a PDF or DOCX.');
        return;
      }
      if (file.size > MAX_SIZE) {
        setResumeFailed('File too large. Maximum size is 10 MB.');
        return;
      }

      setResumeUploading(file.name);

      try {
        const uploadResult = await api.uploadResume(file);
        setResumeProcessing(uploadResult.reference_id);

        // Close any previous SSE connection
        eventSourceRef.current?.close();

        // Stream extraction status via SSE
        const es = new EventSource(api.getExtractionStreamUrl(uploadResult.reference_id));
        eventSourceRef.current = es;

        es.addEventListener('completed', (e: MessageEvent) => {
          const data = JSON.parse(e.data);
          setResumeCompleted(data.structured_data);
          onUploadComplete(uploadResult.reference_id, data.structured_data);
          es.close();
        });

        es.addEventListener('error', (e: Event) => {
          // SSE error can be a MessageEvent (server-sent) or a generic connection error
          if (e instanceof MessageEvent) {
            const data = JSON.parse(e.data);
            setResumeFailed(data.error || 'Extraction failed');
          } else {
            setResumeFailed('Connection to server lost');
          }
          es.close();
        });
      } catch (err) {
        setResumeFailed(err instanceof Error ? err.message : 'Upload failed');
      }
    },
    [onUploadComplete, setResumeUploading, setResumeProcessing, setResumeCompleted, setResumeFailed],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  const onFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  // Completed state
  if (resume.status === 'completed' && resume.structuredData) {
    const data = resume.structuredData;
    return (
      <div className="resume-uploader" role="region" aria-label="Resume Upload">
        <div className="resume-uploader__summary">
          <div className="resume-uploader__summary-name">
            {(data.name as string) || resume.filename || 'Resume'}
          </div>
          {data.current_role ? (
            <div className="resume-uploader__summary-role">
              {String(data.current_role)}
              {data.years_experience ? ` — ${String(data.years_experience)} years` : ''}
            </div>
          ) : null}
          {Array.isArray(data.tech_stack) && data.tech_stack.length > 0 && (
            <div className="resume-uploader__summary-tech">
              {(data.tech_stack as string[]).slice(0, 8).map((tech) => (
                <span key={tech} className="resume-uploader__tech-chip">
                  {tech}
                </span>
              ))}
            </div>
          )}
          <button
            className="btn btn--ghost btn--sm resume-uploader__clear-btn"
            onClick={clearResume}
          >
            Remove
          </button>
        </div>
      </div>
    );
  }

  // Uploading / Processing state
  if (resume.status === 'uploading' || resume.status === 'processing') {
    return (
      <div className="resume-uploader" role="region" aria-label="Resume Upload">
        <div className="resume-uploader__status" aria-live="polite">
          <div className="resume-uploader__spinner" />
          <span>
            {resume.status === 'uploading'
              ? `Uploading ${resume.filename}...`
              : 'Extracting resume data...'}
          </span>
        </div>
      </div>
    );
  }

  // Error state
  if (resume.status === 'failed') {
    return (
      <div className="resume-uploader" role="region" aria-label="Resume Upload">
        <div className="resume-uploader__error" role="alert">
          {resume.error}
        </div>
        <button className="btn btn--ghost btn--sm" onClick={clearResume}>
          Try Again
        </button>
      </div>
    );
  }

  // Idle — drop zone
  return (
    <div
      className={`resume-uploader ${dragging ? 'resume-uploader--dragging' : ''}`}
      role="region"
      aria-label="Resume Upload"
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onClick={() => fileInputRef.current?.click()}
    >
      <div className="resume-uploader__icon">+</div>
      <div className="resume-uploader__text">
        Drop resume here or click to browse
      </div>
      <div className="resume-uploader__text resume-uploader__text--small">
        PDF or DOCX, max 10 MB
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        onChange={onFileSelect}
        style={{ display: 'none' }}
      />
    </div>
  );
}

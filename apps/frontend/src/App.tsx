import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardPage } from '@/pages/DashboardPage';
import { PlanReviewPage } from '@/pages/PlanReviewPage';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { ReportPage } from '@/pages/ReportPage';

export function App() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('panel')) {
      document.documentElement.classList.add('panel-mode');
    }
    return () => {
      document.documentElement.classList.remove('panel-mode');
    };
  }, []);

  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/session/:sessionId/plan" element={<PlanReviewPage />} />
      <Route path="/session/:sessionId" element={<LiveSessionPage />} />
      <Route path="/reports/:reportId" element={<ReportPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

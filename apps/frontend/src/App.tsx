import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardPage } from '@/pages/DashboardPage';
import { PlanReviewPage } from '@/pages/PlanReviewPage';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { ReportPage } from '@/pages/ReportPage';
import { ReportHistoryPage } from '@/pages/ReportHistoryPage';
import { ReportComparisonPage } from '@/components/report/ReportComparison';
import { SessionsPage } from '@/pages/SessionsPage';

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
      <Route path="/" element={<SessionsPage />} />
      <Route path="/new" element={<DashboardPage />} />
      <Route path="/session/:sessionId/plan" element={<PlanReviewPage />} />
      <Route path="/session/:sessionId" element={<LiveSessionPage />} />
      <Route path="/reports" element={<ReportHistoryPage />} />
      <Route path="/reports/compare" element={<ReportComparisonPage />} />
      <Route path="/reports/:reportId" element={<ReportPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

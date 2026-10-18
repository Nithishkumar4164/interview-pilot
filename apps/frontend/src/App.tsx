import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardPage } from '@/pages/DashboardPage';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { ReportPage } from '@/pages/ReportPage';
import { ReportHistoryPage } from '@/pages/ReportHistoryPage';
import { ReportComparisonPage } from '@/components/report/ReportComparison';
import { SessionsPage } from '@/pages/SessionsPage';
import { LoginPage } from '@/pages/LoginPage';
import { AuthCallbackPage } from '@/pages/AuthCallbackPage';
import { RequireAuth } from '@/components/auth/RequireAuth';
import { useAuthStore } from '@/stores/auth-store';

export function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap);

  // Restore the session (refresh cookie) or pick up a pairing token once on load.
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

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
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<SessionsPage />} />
        <Route path="/new" element={<DashboardPage />} />
        <Route path="/session/:sessionId" element={<LiveSessionPage />} />
        <Route path="/reports" element={<ReportHistoryPage />} />
        <Route path="/reports/compare" element={<ReportComparisonPage />} />
        <Route path="/reports/:reportId" element={<ReportPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

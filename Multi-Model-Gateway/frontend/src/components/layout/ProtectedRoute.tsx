import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuthStore } from "@/stores/auth-store";
import { Spinner } from "@/components/ui/Spinner";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const isInitializing = useAuthStore((s) => s.isInitializing);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  if (isInitializing) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Spinner size={28} />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Mail, Lock } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { ApiError } from "@/lib/api-client";
import { AuthLayout } from "@/components/layout/AuthLayout";
import { Input, PasswordInput } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export default function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email.trim(), password);
      navigate("/chat");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.isNetworkError) setError("Connection failed. Is the server running?");
        else if (err.statusCode === 401) setError("Invalid email or password.");
        else setError(err.detail);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to your account"
      footer={
        <>
          Don&apos;t have an account?{" "}
          <Link to="/register" className="text-accent-primary hover:underline">
            Register
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          leftIcon={<Mail size={16} />}
          disabled={loading}
        />
        <PasswordInput
          label="Password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
          leftIcon={<Lock size={16} />}
          disabled={loading}
        />

        {error && (
          <p className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[0.8125rem] text-danger animate-fade-in">
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" fullWidth isLoading={loading} disabled={!email || !password}>
          {loading ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthLayout>
  );
}

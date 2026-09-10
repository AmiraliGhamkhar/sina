import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Mail, Lock } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { ApiError } from "@/lib/api-client";
import { toast } from "@/stores/ui-store";
import { AuthLayout } from "@/components/layout/AuthLayout";
import { Input, PasswordInput } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export default function RegisterPage() {
  const navigate = useNavigate();
  const register = useAuthStore((s) => s.register);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validate = (): string | null => {
    if (!email.includes("@")) return "Please enter a valid email address.";
    if (password.length < 8 || !/[a-zA-Z]/.test(password) || !/\d/.test(password))
      return "Password must be at least 8 characters with a letter and a number.";
    if (password !== confirm) return "Passwords don't match.";
    return null;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const v = validate();
    if (v) {
      setError(v);
      return;
    }
    setLoading(true);
    try {
      await register(email.trim(), password);
      // Backend returns 200 even for already-registered emails (S4), so the
      // toast must not claim an account was created.
      toast.success("Please sign in with your credentials.");
      navigate("/login");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.isNetworkError) setError("Connection failed. Is the server running?");
        else if (err.statusCode === 403) setError("Registration is disabled.");
        else setError("Signup failed — check the address and password.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      title="Create an account"
      subtitle="Start using AI inference"
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="text-accent-primary hover:underline">
            Sign in
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
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
          hint="At least 8 characters with a letter and a number"
          leftIcon={<Lock size={16} />}
          disabled={loading}
        />
        <PasswordInput
          label="Confirm Password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder="••••••••"
          leftIcon={<Lock size={16} />}
          disabled={loading}
        />

        {error && (
          <p className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[0.8125rem] text-danger animate-fade-in">
            {error}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          fullWidth
          isLoading={loading}
          disabled={!email || !password || !confirm}
        >
          {loading ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthLayout>
  );
}

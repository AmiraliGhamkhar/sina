import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/Button";

export default function NotFoundPage() {
  return (
    <div className="flex min-h-screen w-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-bg-tertiary">
        <Compass size={30} className="text-accent-primary" />
      </span>
      <h1 className="text-5xl text-text-primary">404</h1>
      <p className="text-sm text-text-secondary">This page could not be found.</p>
      <Link to="/chat">
        <Button variant="primary">Back to chat</Button>
      </Link>
    </div>
  );
}

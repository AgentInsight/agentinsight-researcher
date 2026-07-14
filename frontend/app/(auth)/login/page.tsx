// app/(auth)/login/page.tsx
import { LoginForm } from "@/components/auth/login-form";

/**
 * 登录页 (SELF_HOST=false 时使用)
 * SELF_HOST=true 时 middleware 会跳过此页, 直接进 /chat
 */
export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <LoginForm />
    </div>
  );
}

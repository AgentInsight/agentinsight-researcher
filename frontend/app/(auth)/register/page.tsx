// app/(auth)/register/page.tsx
import { RegisterForm } from "@/components/auth/register-form";

/**
 * 注册页 (SELF_HOST=false 时使用)
 */
export default function RegisterPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <RegisterForm />
    </div>
  );
}

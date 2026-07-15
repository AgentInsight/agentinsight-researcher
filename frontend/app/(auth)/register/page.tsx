// app/(auth)/register/page.tsx
import { RegisterForm } from "@/components/auth/register-form";
import { AuthLayout } from "@/components/auth/auth-layout";

/**
 * 注册页 (复制自 traceability-platform)
 * - 使用 AuthLayout (光晕背景 + 品牌元素 + 切换链接 + 版权)
 */
export default function RegisterPage() {
  return (
    <AuthLayout mode="register" switchHref="/login">
      <RegisterForm />
    </AuthLayout>
  );
}

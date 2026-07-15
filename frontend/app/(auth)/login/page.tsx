// app/(auth)/login/page.tsx
import { LoginForm } from "@/components/auth/login-form";
import { AuthLayout } from "@/components/auth/auth-layout";

/**
 * 登录页 (复制自 traceability-platform)
 * - 使用 AuthLayout (光晕背景 + 品牌元素 + 切换链接 + 版权)
 * - SELF_HOST=true 时 middleware 会跳过此页, 直接进 /agent/researcher/chat
 */
export default function LoginPage() {
  return (
    <AuthLayout mode="login" switchHref="/register">
      <LoginForm />
    </AuthLayout>
  );
}

import { EditOutlined, LockOutlined, LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { Dropdown, Form, Input, Modal, message } from "antd";
import type { MenuProps } from "antd";
import { useState } from "react";
import { updateMyNickname, updateMyPassword } from "@/api/auth";

type Props = {
  account: string;
  name: string;
  onNameChange: (name: string) => void;
  onLogout: () => void;
};

export default function UserAvatarMenu({ account, name, onNameChange, onLogout }: Props) {
  const [nameOpen, setNameOpen] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [nameForm] = Form.useForm<{ name: string }>();
  const [pwdForm] = Form.useForm<{ old_password: string; new_password: string; confirm: string }>();

  const items: MenuProps["items"] = [
    { key: "nickname", icon: <EditOutlined />, label: "修改昵称" },
    { key: "password", icon: <LockOutlined />, label: "修改密码" },
    { type: "divider" },
    { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true }
  ];

  const onClick: MenuProps["onClick"] = ({ key }) => {
    if (key === "logout") {
      onLogout();
      return;
    }
    if (key === "nickname") {
      nameForm.setFieldsValue({ name: name || account });
      setNameOpen(true);
      return;
    }
    if (key === "password") {
      pwdForm.resetFields();
      setPwdOpen(true);
    }
  };

  const saveName = async () => {
    try {
      const values = await nameForm.validateFields();
      setSaving(true);
      const res = await updateMyNickname(values.name.trim());
      onNameChange(res.name);
      message.success("昵称已更新");
      setNameOpen(false);
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const savePassword = async () => {
    try {
      const values = await pwdForm.validateFields();
      setSaving(true);
      await updateMyPassword({
        old_password: values.old_password,
        new_password: values.new_password
      });
      message.success("密码已更新");
      setPwdOpen(false);
      pwdForm.resetFields();
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Dropdown menu={{ items, onClick }} trigger={["click"]} placement="bottomRight">
        <button
          type="button"
          className="flex h-9 items-center gap-2 rounded-full border border-[#d9e2f0] bg-[#f7faff] pl-1.5 pr-3 text-[#3a465d] shadow-sm transition-colors hover:bg-[#eef4ff] dark:border-[#2f3a52] dark:bg-[#1a2130] dark:text-gray-200 dark:hover:bg-[#222b3d]"
          aria-label="用户菜单"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-500 text-[15px] text-white">
            <UserOutlined />
          </span>
          {account ? (
            <span className="max-w-[140px] truncate text-sm font-medium">{account}</span>
          ) : null}
        </button>
      </Dropdown>

      <Modal
        title="修改昵称"
        open={nameOpen}
        onCancel={() => setNameOpen(false)}
        onOk={() => void saveName()}
        confirmLoading={saving}
        destroyOnClose
        afterOpenChange={(open) => {
          if (open) nameForm.setFieldsValue({ name: name || account });
        }}
      >
        <Form form={nameForm} layout="vertical" className="mt-2">
          <Form.Item
            name="name"
            label="昵称"
            rules={[
              { required: true, message: "请输入昵称" },
              { max: 255, message: "昵称过长" }
            ]}
          >
            <Input placeholder="显示名称" maxLength={255} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="修改密码"
        open={pwdOpen}
        onCancel={() => setPwdOpen(false)}
        onOk={() => void savePassword()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={pwdForm} layout="vertical" className="mt-2">
          <Form.Item
            name="old_password"
            label="原密码"
            rules={[{ required: true, message: "请输入原密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 6, message: "新密码至少 6 位" }
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("new_password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("两次输入的密码不一致"));
                }
              })
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

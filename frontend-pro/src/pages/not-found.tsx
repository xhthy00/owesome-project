import { Button, Result } from 'antd';
import { Link } from '@umijs/max';

export default function NotFound() {
  return <Result status="404" title="页面不存在" extra={<Link to="/"><Button type="primary">返回学情总览</Button></Link>} />;
}

<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="en" sourcelanguage="zh_CN">
<context>
    <name>AboutPage</name>
    <message>
        <source>关于</source>
        <translation>About</translation>
    </message>
    <message>
        <source>凝胶智检系统（GelInspect）</source>
        <translation>GelInspect</translation>
    </message>
    <message>
        <source>凝胶法细菌内毒素检测的上位机软件：试验管理、视频采集、算法判读、复核与报告。</source>
        <translation>Host software for gel-clot bacterial endotoxin testing: experiment management, video capture, algorithmic reading, review and reports.</translation>
    </message>
    <message>
        <source>版本 {version}</source>
        <translation>Version {version}</translation>
    </message>
    <message>
        <source>运行环境</source>
        <translation>Environment</translation>
    </message>
    <message>
        <source>开源许可</source>
        <translation>Open-source licenses</translation>
    </message>
    <message>
        <source>系统</source>
        <translation>System</translation>
    </message>
    <message>
        <source>Python</source>
        <translation>Python</translation>
    </message>
    <message>
        <source>Qt / PySide6</source>
        <translation>Qt / PySide6</translation>
    </message>
    <message>
        <source>数据目录</source>
        <translation>Data folder</translation>
    </message>
    <message>
        <source>日志目录</source>
        <translation>Log folder</translation>
    </message>
    <message>
        <source>设备后端</source>
        <translation>Device backend</translation>
    </message>
    <message>
        <source>视频源</source>
        <translation>Video source</translation>
    </message>
    <message>
        <source>算法后端</source>
        <translation>Analysis backend</translation>
    </message>
</context>
<context>
    <name>Alert</name>
    <message>
        <source>设备故障</source>
        <translation>Device fault</translation>
    </message>
    <message>
        <source>翻转复位失败，该排可能仍处于翻转状态，需要人工检查</source>
        <translation>Flip reset failed; the row may still be flipped and needs a manual check</translation>
    </message>
    <message>
        <source>退出滑台失败</source>
        <translation>Failed to move the tray out</translation>
    </message>
    <message>
        <source>视频断流</source>
        <translation>Video stream lost</translation>
    </message>
    <message>
        <source>视频已重连</source>
        <translation>Video reconnected</translation>
    </message>
    <message>
        <source>视频质量告警</source>
        <translation>Video quality warning</translation>
    </message>
    <message>
        <source>录制段异常</source>
        <translation>Recording segment error</translation>
    </message>
    <message>
        <source>读数失败</source>
        <translation>Reading failed</translation>
    </message>
    <message>
        <source>读数晚于到时过久</source>
        <translation>Reading started much later than due</translation>
    </message>
    <message>
        <source>温度超出允许范围</source>
        <translation>Temperature outside the allowed range</translation>
    </message>
    <message>
        <source>温度回到范围内</source>
        <translation>Temperature back in range</translation>
    </message>
    <message>
        <source>分析超时，继续等待结果</source>
        <translation>Analysis timed out; still waiting for the result</translation>
    </message>
    <message>
        <source>算法分析失败，该排转人工复核</source>
        <translation>Analysis failed; the row goes to manual review</translation>
    </message>
    <message>
        <source>算法结果不符合接口约定</source>
        <translation>Analysis result violates the interface contract</translation>
    </message>
    <message>
        <source>恢复请求被拒绝</source>
        <translation>Resume request rejected</translation>
    </message>
    <message>
        <source>算法线程未在时限内退出，它会在算法返回后自行结束</source>
        <translation>The analysis thread did not exit in time; it ends when the analysis returns</translation>
    </message>
    <message>
        <source>写入数据库失败</source>
        <translation>Failed to write to the database</translation>
    </message>
    <message>
        <source>数据库被其他操作占用，正在等待写入</source>
        <translation>The database is busy; waiting to write</translation>
    </message>
    <message>
        <source>程序内部错误</source>
        <translation>Internal error</translation>
    </message>
    <message>
        <source>舱门被打开，到时的排等舱门关闭后才读数</source>
        <translation>The door is open; rows that are due will be read after it closes</translation>
    </message>
    <message>
        <source>舱门已关闭</source>
        <translation>The door is closed</translation>
    </message>
    <message>
        <source>画面过暗（黑屏）</source>
        <translation>Image too dark (black screen)</translation>
    </message>
    <message>
        <source>画面过曝</source>
        <translation>Image overexposed</translation>
    </message>
    <message>
        <source>画面长时间没有变化（卡帧）</source>
        <translation>Image has not changed for a long time (frozen)</translation>
    </message>
    <message>
        <source>视频帧率过低</source>
        <translation>Video frame rate too low</translation>
    </message>
    <message>
        <source>视频源丢帧</source>
        <translation>Video source dropped frames</translation>
    </message>
    <message>
        <source>很久没有收到新画面</source>
        <translation>No new image received for a long time</translation>
    </message>
    <message>
        <source>正在重连视频</source>
        <translation>Reconnecting video</translation>
    </message>
    <message>
        <source>视频无法恢复</source>
        <translation>Video cannot be recovered</translation>
    </message>
    <message>
        <source>抓拍缓存溢出，抓拍帧可能丢失</source>
        <translation>Capture buffer overflow; captured frames may be lost</translation>
    </message>
    <message>
        <source>视频处理出错</source>
        <translation>Video processing error</translation>
    </message>
    <message>
        <source>视频回调出错</source>
        <translation>Video callback error</translation>
    </message>
    <message>
        <source>录制队列已满，丢弃了录制帧</source>
        <translation>Recording queue full; recorded frames were dropped</translation>
    </message>
    <message>
        <source>录制段不完整</source>
        <translation>Recording segment incomplete</translation>
    </message>
    <message>
        <source>视频线程没有及时停止</source>
        <translation>A video thread did not stop in time</translation>
    </message>
</context>
<context>
    <name>AlertStack</name>
    <message>
        <source>另有 {n} 条告警</source>
        <translation>{n} more alerts</translation>
    </message>
</context>
<context>
    <name>AnalysisStatus</name>
    <message>
        <source>分析成功</source>
        <translation>Analyzed</translation>
    </message>
    <message>
        <source>凝胶缺失</source>
        <translation>Gel missing</translation>
    </message>
    <message>
        <source>未找到试管</source>
        <translation>Tube not found</translation>
    </message>
    <message>
        <source>图像质量不足</source>
        <translation>Low image quality</translation>
    </message>
    <message>
        <source>分析失败</source>
        <translation>Analysis failed</translation>
    </message>
</context>
<context>
    <name>AppShell</name>
    <message>
        <source>取消固定，侧栏改为自动隐藏</source>
        <translation>Unpin: auto-hide the sidebar</translation>
    </message>
    <message>
        <source>展开侧栏</source>
        <translation>Show sidebar</translation>
    </message>
    <message>
        <source>收起侧栏</source>
        <translation>Hide sidebar</translation>
    </message>
</context>
<context>
    <name>AuditPage</name>
    <message>
        <source>没有符合条件的审计记录</source>
        <translation>No audit records match</translation>
    </message>
    <message>
        <source>调整查询条件，或点“重置”清除条件后再查</source>
        <translation>Adjust the filters, or click Reset to clear them and search again</translation>
    </message>
    <message>
        <source>还没有审计记录</source>
        <translation>No audit records yet</translation>
    </message>
    <message>
        <source>正在查询……</source>
        <translation>Searching…</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Your role does not have this permission</translation>
    </message>
    <message>
        <source>正在校验完整性……</source>
        <translation>Verifying integrity…</translation>
    </message>
    <message>
        <source>正在校验完整性：{done} / {total}</source>
        <translation>Verifying integrity: {done} / {total}</translation>
    </message>
    <message>
        <source>校验通过：哈希链完好</source>
        <translation>Verification passed: the hash chain is intact</translation>
    </message>
    <message>
        <source>共校验 {count} 条记录，没有发现被改动或删除的记录。</source>
        <translation>{count} records verified. No record was altered or deleted.</translation>
    </message>
    <message>
        <source>链头记录缺失</source>
        <translation>The chain head record is missing</translation>
    </message>
    <message>
        <source>第一处异常在序号 {seq}</source>
        <translation>The first problem is at No. {seq}</translation>
    </message>
    <message>
        <source>校验未通过：审计记录可能被改动或删除</source>
        <translation>Verification failed: audit records may have been altered or deleted</translation>
    </message>
    <message>
        <source>校验没有完成</source>
        <translation>Verification did not complete</translation>
    </message>
    <message>
        <source>全部记录使用密钥（HMAC-SHA256）链接。</source>
        <translation>All records are chained with a key (HMAC-SHA256).</translation>
    </message>
    <message>
        <source>序号 {seq} 之前的旧记录使用 SHA-256，从序号 {seq} 起使用密钥（HMAC-SHA256）。</source>
        <translation>Records before #{seq} use SHA-256; from #{seq} on they are chained with a key (HMAC-SHA256).</translation>
    </message>
    <message>
        <source>记录使用 SHA-256 链接（未启用密钥）。</source>
        <translation>Records are chained with SHA-256 (no key).</translation>
    </message>
    <message>
        <source>这条记录的内容与它的哈希不符，内容可能被改动。</source>
        <translation>This record&apos;s content does not match its hash; it may have been altered.</translation>
    </message>
    <message>
        <source>这条记录与上一条的哈希衔接不上，前一条可能被改动。</source>
        <translation>This record does not link to the previous one; the previous record may have been altered.</translation>
    </message>
    <message>
        <source>序号不连续，这个序号的记录可能被删除。</source>
        <translation>The numbering has a gap; the record with this number may have been deleted.</translation>
    </message>
    <message>
        <source>链头与最后一条记录不符，末尾的记录可能被删除。</source>
        <translation>The chain head does not match the last record; trailing records may have been deleted.</translation>
    </message>
    <message>
        <source>链的类型或切换点与密钥文件的记录不符，审计链可能被降级后整条重算。</source>
        <translation>The chain type or switch point does not match the key file. The chain may have been downgraded and recomputed.</translation>
    </message>
    <message>
        <source>这一段记录用密钥计算，但密钥不可用（密钥文件丢失，或换了电脑、系统用户），无法校验。</source>
        <translation>These records are keyed, but the key is unavailable (key file lost, or a different computer or user), so they cannot be verified.</translation>
    </message>
    <message>
        <source>导出审计追踪</source>
        <translation>Export audit trail</translation>
    </message>
    <message>
        <source>Excel 文件 (*.xlsx)</source>
        <translation>Excel files (*.xlsx)</translation>
    </message>
    <message>
        <source>已导出 {rows} 条：{name}</source>
        <translation>Exported {rows} records: {name}</translation>
    </message>
    <message>
        <source>操作失败：{reason}</source>
        <translation>Operation failed: {reason}</translation>
    </message>
    <message>
        <source>序号</source>
        <translation>No.</translation>
    </message>
    <message>
        <source>操作者</source>
        <translation>Actor</translation>
    </message>
    <message>
        <source>时间</source>
        <translation>Time</translation>
    </message>
    <message>
        <source>权限</source>
        <translation>Permission</translation>
    </message>
    <message>
        <source>操作记录</source>
        <translation>Action</translation>
    </message>
    <message>
        <source>审计追踪</source>
        <translation>Audit Trail</translation>
    </message>
    <message>
        <source>校验完整性</source>
        <translation>Verify integrity</translation>
    </message>
    <message>
        <source>导出 Excel…</source>
        <translation>Export Excel…</translation>
    </message>
    <message>
        <source>开始日期</source>
        <translation>From date</translation>
    </message>
    <message>
        <source>结束日期</source>
        <translation>To date</translation>
    </message>
    <message>
        <source>全部操作者</source>
        <translation>All actors</translation>
    </message>
    <message>
        <source>关键字</source>
        <translation>Keyword</translation>
    </message>
    <message>
        <source>操作记录或试验编号</source>
        <translation>Action or experiment ID</translation>
    </message>
    <message>
        <source>查询</source>
        <translation>Search</translation>
    </message>
    <message>
        <source>重置</source>
        <translation>Reset</translation>
    </message>
</context>
<context>
    <name>AuthError</name>
    <message>
        <source>用户名或密码错误，还可以尝试 {remaining} 次</source>
        <translation>Wrong username or password. {remaining} attempts left.</translation>
    </message>
    <message>
        <source>失败次数过多，已暂时锁定，请 {minutes} 分钟后再试</source>
        <translation>Too many failed attempts. Locked, try again in {minutes} min.</translation>
    </message>
    <message>
        <source>账号已停用，请联系管理员</source>
        <translation>This account is disabled. Contact an administrator.</translation>
    </message>
    <message>
        <source>登录名不能为空、不能含空白，且不超过 {max} 个字符</source>
        <translation>Username must not be empty or contain spaces, max {max} characters.</translation>
    </message>
    <message>
        <source>密码不能少于 {min} 个字符</source>
        <translation>Password must be at least {min} characters.</translation>
    </message>
    <message>
        <source>新密码不能与当前密码相同</source>
        <translation>The new password must differ from the current one.</translation>
    </message>
    <message>
        <source>新密码不能与登录名相同</source>
        <translation>The new password must differ from the username.</translation>
    </message>
    <message>
        <source>请先登录</source>
        <translation>Please sign in first.</translation>
    </message>
    <message>
        <source>会话已失效，请重新登录</source>
        <translation>Session expired. Please sign in again.</translation>
    </message>
    <message>
        <source>屏幕已锁定，请先解锁</source>
        <translation>The screen is locked. Unlock it first.</translation>
    </message>
    <message>
        <source>请先修改初始密码</source>
        <translation>Change the initial password first.</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
    <message>
        <source>系统已经初始化，不能再创建首个管理员</source>
        <translation>The system is already initialized.</translation>
    </message>
    <message>
        <source>当前配置不允许在界面创建管理员</source>
        <translation>The current configuration does not allow creating an administrator here.</translation>
    </message>
    <message>
        <source>数据库正忙，操作没有生效，请稍后再试</source>
        <translation>The database is busy. Nothing was changed; try again shortly.</translation>
    </message>
</context>
<context>
    <name>BatchExportDialog</name>
    <message>
        <source>选择导出目录</source>
        <translation>Choose export folder</translation>
    </message>
    <message>
        <source>请至少勾选一份报告。</source>
        <translation>Select at least one report.</translation>
    </message>
    <message>
        <source>请先选择导出目录。</source>
        <translation>Choose an export folder first.</translation>
    </message>
    <message>
        <source>已导出 {n} 份到 {dir}，用时 {s:.2f} 秒。清单：{manifest}</source>
        <translation>Exported {n} report(s) to {dir} in {s:.2f} s. Manifest: {manifest}</translation>
    </message>
    <message>
        <source>文件不存在</source>
        <translation>file missing</translation>
    </message>
    <message>
        <source>与入库的哈希不符（可能被改动）</source>
        <translation>does not match the stored hash (possibly modified)</translation>
    </message>
    <message>
        <source>没有签名</source>
        <translation>not signed</translation>
    </message>
    <message>
        <source>没有这条记录</source>
        <translation>no such record</translation>
    </message>
    <message>
        <source>有 {n} 份没有导出：</source>
        <translation>{n} report(s) were not exported: </translation>
    </message>
    <message>
        <source>批量导出报告</source>
        <translation>Export reports in bulk</translation>
    </message>
    <message>
        <source>把勾选的已签署报告复制到一个目录，并写一份清单 CSV（文件名、SHA-256、签署人和时间）。文件与库里的哈希不符时不导出。</source>
        <translation>Copies the selected signed reports to a folder and writes a manifest CSV (file name, SHA-256, signer and time). Files that do not match the stored hash are not exported.</translation>
    </message>
    <message>
        <source>全选</source>
        <translation>Select all</translation>
    </message>
    <message>
        <source>全不选</source>
        <translation>Select none</translation>
    </message>
    <message>
        <source>导出目录</source>
        <translation>Export folder</translation>
    </message>
    <message>
        <source>浏览…</source>
        <translation>Browse…</translation>
    </message>
    <message>
        <source>关闭</source>
        <translation>Close</translation>
    </message>
    <message>
        <source>导出</source>
        <translation>Export</translation>
    </message>
</context>
<context>
    <name>ChangePasswordDialog</name>
    <message>
        <source>两次输入的新密码不一致</source>
        <translation>The new passwords do not match.</translation>
    </message>
    <message>
        <source>修改密码</source>
        <translation>Change password</translation>
    </message>
    <message>
        <source>这是初始密码，请先设置自己的新密码，之后才能使用系统。</source>
        <translation>This is an initial password. Set your own new password before using the system.</translation>
    </message>
    <message>
        <source>退出登录</source>
        <translation>Sign out</translation>
    </message>
    <message>
        <source>请输入当前密码和新密码。</source>
        <translation>Enter your current password and a new one.</translation>
    </message>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>当前密码</source>
        <translation>Current password</translation>
    </message>
    <message>
        <source>新密码（至少 8 个字符）</source>
        <translation>New password (at least 8 characters)</translation>
    </message>
    <message>
        <source>确认新密码</source>
        <translation>Confirm new password</translation>
    </message>
</context>
<context>
    <name>Conclusion</name>
    <message>
        <source>合格</source>
        <translation>Pass</translation>
    </message>
    <message>
        <source>不合格</source>
        <translation>Fail</translation>
    </message>
    <message>
        <source>无效</source>
        <translation>Invalid</translation>
    </message>
    <message>
        <source>待定</source>
        <translation>Pending</translation>
    </message>
    <message>
        <source>标示灵敏度 λ = {lam} EU/mL</source>
        <translation>Labeled sensitivity λ = {lam} EU/mL</translation>
    </message>
    <message>
        <source>第 {series} 系列（{tubes} 号管）：{readings}</source>
        <translation>Series {series} (tubes {tubes}): {readings}</translation>
    </message>
    <message>
        <source>第 {series} 系列（{tubes} 号管）：{readings}，终点 {endpoint} EU/mL（{multiple}λ）</source>
        <translation>Series {series} (tubes {tubes}): {readings}, endpoint {endpoint} EU/mL ({multiple}λ)</translation>
    </message>
    <message>
        <source>阴性对照（{tubes} 号管）：全部阴性</source>
        <translation>Negative control (tube {tubes}): all negative</translation>
    </message>
    <message>
        <source>λc = antilg(mean(lg 终点)) = antilg(({terms}) / {count}) ≈ {value}</source>
        <translation>λc = antilg(mean(lg endpoint)) = antilg(({terms}) / {count}) ≈ {value}</translation>
    </message>
    <message>
        <source>λc = {value} EU/mL，判定范围 0.5λ–2λ（含）= {low}–{high} EU/mL</source>
        <translation>λc = {value} EU/mL; acceptance range 0.5λ–2λ (inclusive) = {low}–{high} EU/mL</translation>
    </message>
    <message>
        <source>{role}（{tubes} 号管）：阳性 {positive}，阴性 {negative}，待定 {other}</source>
        <translation>{role} (tubes {tubes}): positive {positive}, negative {negative}, undetermined {other}</translation>
    </message>
    <message>
        <source>中国药典通则 1143</source>
        <translation>Chinese Pharmacopoeia General Chapter 1143</translation>
    </message>
    <message>
        <source>没有安排试管</source>
        <translation>No tubes are assigned</translation>
    </message>
    <message>
        <source>试验使用的旧模板没有标明每管用途，不能判定</source>
        <translation>The experiment uses an old template without tube roles, so no conclusion can be given</translation>
    </message>
    <message>
        <source>{tubes} 号管没有指定用途</source>
        <translation>Tubes {tubes} have no role assigned</translation>
    </message>
    <message>
        <source>旧版本软件没有这类试验的判定规则，不给出合格或不合格</source>
        <translation>An older software version had no rule for this test type; no pass or fail is given</translation>
    </message>
    <message>
        <source>{tubes} 号管还没有结果</source>
        <translation>Tubes {tubes} have no result yet</translation>
    </message>
    <message>
        <source>{tubes} 号管等待人工复核</source>
        <translation>Tubes {tubes} are awaiting manual review</translation>
    </message>
    <message>
        <source>没有可用的鲎试剂标示灵敏度 λ</source>
        <translation>No usable labeled sensitivity λ for the LAL reagent</translation>
    </message>
    <message>
        <source>管位与规则要求的布局不符：阴性对照 {tubes} 号管加了内毒素</source>
        <translation>Layout does not match the rule: negative control tube {tubes} has endotoxin added</translation>
    </message>
    <message>
        <source>管位与规则要求的布局不符：{tubes} 号管的外加内毒素不是 2λ、λ、λ/2、λ/4 之一</source>
        <translation>Layout does not match the rule: the spiked endotoxin of tube {tubes} is not one of 2λ, λ, λ/2, λ/4</translation>
    </message>
    <message>
        <source>管位与规则要求的布局不符：{multiple}λ 应有 {expected} 管，实际 {actual} 管</source>
        <translation>Layout does not match the rule: {multiple}λ should have {expected} tubes, found {actual}</translation>
    </message>
    <message>
        <source>模板没有标记阴性对照</source>
        <translation>The template has no negative control marked</translation>
    </message>
    <message>
        <source>最大浓度 {level} EU/mL 不是全部阳性（{tubes} 号管不是阳性）</source>
        <translation>The highest concentration {level} EU/mL is not all positive (tubes {tubes} are not positive)</translation>
    </message>
    <message>
        <source>最低浓度 {level} EU/mL 不是全部阴性（{tubes} 号管不是阴性）</source>
        <translation>The lowest concentration {level} EU/mL is not all negative (tubes {tubes} are not negative)</translation>
    </message>
    <message>
        <source>阴性对照不是阴性（{tubes} 号管）</source>
        <translation>The negative control is not negative (tubes {tubes})</translation>
    </message>
    <message>
        <source>第 {series} 系列不连续（阴性之后又出现阳性），需人工判定；上面的 λc 只作参考</source>
        <translation>Series {series} is discontinuous (positive after negative) and needs a manual decision; the λc is for reference only</translation>
    </message>
    <message>
        <source>模板没有标记{role}</source>
        <translation>The template has no tubes marked as {role}</translation>
    </message>
    <message>
        <source>{role}不是全部阴性（{tubes} 号管）</source>
        <translation>{role} is not all negative (tubes {tubes})</translation>
    </message>
    <message>
        <source>{role}不是全部阳性（{tubes} 号管）</source>
        <translation>{role} is not all positive (tubes {tubes})</translation>
    </message>
    <message>
        <source>{role}（{tubes} 号管）：全部阴性</source>
        <translation>{role} (tubes {tubes}): all negative</translation>
    </message>
    <message>
        <source>{role}（{tubes} 号管）：全部阳性</source>
        <translation>{role} (tubes {tubes}): all positive</translation>
    </message>
    <message>
        <source>{role}：各浓度或稀释度的管数应相同，实际为 {counts}</source>
        <translation>{role}: every concentration or dilution needs the same number of tubes; found {counts}</translation>
    </message>
    <message>
        <source>{role}第 {series} 系列（{tubes} 号管）：{readings}，终点 {endpoint} EU/mL</source>
        <translation>{role} series {series} (tubes {tubes}): {readings}, endpoint {endpoint} EU/mL</translation>
    </message>
    <message>
        <source>{role}第 {series} 系列（{tubes} 号管）：{readings}，没有阳性</source>
        <translation>{role} series {series} (tubes {tubes}): {readings}, no positive</translation>
    </message>
    <message>
        <source>{role}第 {series} 系列不连续（阴性之后又出现阳性），需人工判定</source>
        <translation>{role} series {series} is discontinuous (positive after negative) and needs a manual decision</translation>
    </message>
    <message>
        <source>Es = antilg(mean(lg 终点)) ≈ {value} EU/mL，应在 0.5λ–2λ（{low}–{high} EU/mL）内</source>
        <translation>Es = antilg(mean(lg endpoint)) ≈ {value} EU/mL; must be within 0.5λ–2λ ({low}–{high} EU/mL)</translation>
    </message>
    <message>
        <source>Es = {value} EU/mL 超出 0.5λ–2λ（{low}–{high} EU/mL）</source>
        <translation>Es = {value} EU/mL is outside 0.5λ–2λ ({low}–{high} EU/mL)</translation>
    </message>
    <message>
        <source>Et = antilg(mean(lg 终点)) ≈ {value} EU/mL，判定范围 0.5Es–2Es（{low}–{high} EU/mL）</source>
        <translation>Et = antilg(mean(lg endpoint)) ≈ {value} EU/mL; acceptance range 0.5Es–2Es ({low}–{high} EU/mL)</translation>
    </message>
    <message>
        <source>Et 在 0.5Es–2Es 内：供试品在该浓度下无干扰作用</source>
        <translation>Et is within 0.5Es–2Es: the sample does not interfere at this concentration</translation>
    </message>
    <message>
        <source>Et 超出 0.5Es–2Es：供试品在该浓度下存在干扰作用</source>
        <translation>Et is outside 0.5Es–2Es: the sample interferes at this concentration</translation>
    </message>
    <message>
        <source>供试品内毒素系列第 {series} 系列没有阳性，无法计算 Et：供试品在该浓度下存在干扰作用</source>
        <translation>Spiked sample series {series} has no positive, so Et cannot be calculated: the sample interferes at this concentration</translation>
    </message>
    <message>
        <source>供试品（{tubes} 号管）全部阴性：符合规定</source>
        <translation>Sample (tubes {tubes}) all negative: complies</translation>
    </message>
    <message>
        <source>供试品（{tubes} 号管）有阳性：不符合规定</source>
        <translation>Sample (tubes {tubes}) has a positive: does not comply</translation>
    </message>
    <message>
        <source>供试品两管一阳一阴，需用“限度试验（复试）”模板复试</source>
        <translation>One of the two sample tubes is positive: retest with the “Limit test (retest)” template</translation>
    </message>
    <message>
        <source>供试品 {tubes} 号管没有稀释倍数</source>
        <translation>Sample tubes {tubes} have no dilution factor</translation>
    </message>
    <message>
        <source>供试品第 {series} 系列（{tubes} 号管）：{readings}，终点稀释 {dilution} 倍，浓度 {value} EU/mL</source>
        <translation>Sample series {series} (tubes {tubes}): {readings}, endpoint dilution {dilution}×, concentration {value} EU/mL</translation>
    </message>
    <message>
        <source>供试品第 {series} 系列（{tubes} 号管）：全部阴性，浓度 &lt; {value} EU/mL</source>
        <translation>Sample series {series} (tubes {tubes}): all negative, concentration &lt; {value} EU/mL</translation>
    </message>
    <message>
        <source>供试品第 {series} 系列（{tubes} 号管）：全部阳性，浓度 ≥ {value} EU/mL</source>
        <translation>Sample series {series} (tubes {tubes}): all positive, concentration ≥ {value} EU/mL</translation>
    </message>
    <message>
        <source>供试品内毒素浓度 = antilg(mean(lg 各系列浓度)) ≈ {value} EU/mL</source>
        <translation>Sample endotoxin concentration = antilg(mean(lg series concentrations)) ≈ {value} EU/mL</translation>
    </message>
    <message>
        <source>没有录入内毒素限值，无法判定是否符合规定</source>
        <translation>No endotoxin limit was entered, so compliance cannot be judged</translation>
    </message>
    <message>
        <source>各系列浓度均小于内毒素限值 {limit} EU/mL：符合规定</source>
        <translation>Every series is below the endotoxin limit {limit} EU/mL: complies</translation>
    </message>
    <message>
        <source>第 {series} 系列浓度不小于内毒素限值 {limit} EU/mL：不符合规定</source>
        <translation>Series {series} is not below the endotoxin limit {limit} EU/mL: does not comply</translation>
    </message>
    <message>
        <source>第 {series} 系列无法与内毒素限值 {limit} EU/mL 比较，需调整稀释倍数后重新试验</source>
        <translation>Series {series} cannot be compared with the endotoxin limit {limit} EU/mL; adjust the dilutions and repeat the test</translation>
    </message>
    <message>
        <source>λc = {value} EU/mL 超出 0.5λ–2λ（{low}–{high} EU/mL）</source>
        <translation>λc = {value} EU/mL is outside 0.5λ–2λ ({low}–{high} EU/mL)</translation>
    </message>
    <message>
        <source>这个试验完成时还没有结论功能，没有入库的结论</source>
        <translation>This experiment finished before conclusions were recorded; no conclusion is stored</translation>
    </message>
    <message>
        <source>试验已停止，不产生结论</source>
        <translation>The experiment was stopped; no conclusion is produced</translation>
    </message>
</context>
<context>
    <name>ConclusionCard</name>
    <message>
        <source>规则版本 {version}</source>
        <translation>Rule version {version}</translation>
    </message>
    <message>
        <source>实测灵敏度 λc</source>
        <translation>Measured sensitivity λc</translation>
    </message>
    <message>
        <source>λc（参考值，不是最终结论）</source>
        <translation>λc (reference only, not a final conclusion)</translation>
    </message>
    <message>
        <source>标示灵敏度 λ {value} EU/ml</source>
        <translation>Labeled sensitivity λ {value} EU/ml</translation>
    </message>
    <message>
        <source>无效原因</source>
        <translation>Why it is invalid</translation>
    </message>
    <message>
        <source>未满足的前提</source>
        <translation>Unmet prerequisites</translation>
    </message>
    <message>
        <source>规则来源：{source}</source>
        <translation>Rule source: {source}</translation>
    </message>
    <message>
        <source>试验完成后才会固化结论。</source>
        <translation>The conclusion is fixed when the experiment completes.</translation>
    </message>
    <message>
        <source>试验结论</source>
        <translation>Conclusion</translation>
    </message>
    <message>
        <source>依据（+ 阳性，− 阴性）</source>
        <translation>Basis (+ positive, − negative)</translation>
    </message>
</context>
<context>
    <name>ConfirmDialog</name>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>确认</source>
        <translation>Confirm</translation>
    </message>
</context>
<context>
    <name>CredentialDialog</name>
    <message>
        <source>临时密码</source>
        <translation>Temporary password</translation>
    </message>
    <message>
        <source>{name} 的临时密码</source>
        <translation>Temporary password for {name}</translation>
    </message>
    <message>
        <source>请当面告知用户。对方首次登录时必须先修改密码。</source>
        <translation>Tell the user in person. They must change it at their first sign-in.</translation>
    </message>
    <message>
        <source>这个密码只显示这一次，关闭后无法再查看；忘了只能重新重置。</source>
        <translation>This password is shown only once and cannot be viewed again after closing; if it is lost, reset it again.</translation>
    </message>
    <message>
        <source>复制</source>
        <translation>Copy</translation>
    </message>
    <message>
        <source>关闭</source>
        <translation>Close</translation>
    </message>
</context>
<context>
    <name>DashboardPage</name>
    <message>
        <source>试验</source>
        <translation>Experiment</translation>
    </message>
    <message>
        <source>类型</source>
        <translation>Type</translation>
    </message>
    <message>
        <source>试管</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>结果</source>
        <translation>Result</translation>
    </message>
    <message>
        <source>状态</source>
        <translation>Status</translation>
    </message>
    <message>
        <source>完成时间</source>
        <translation>Completed</translation>
    </message>
    <message>
        <source>工作台</source>
        <translation>Dashboard</translation>
    </message>
    <message>
        <source>新建试验</source>
        <translation>New experiment</translation>
    </message>
    <message>
        <source>检测室温度</source>
        <translation>Chamber temperature</translation>
    </message>
    <message>
        <source>进行中的试验</source>
        <translation>Experiment in progress</translation>
    </message>
    <message>
        <source>打开监控</source>
        <translation>Open monitor</translation>
    </message>
    <message>
        <source>最近结果</source>
        <translation>Recent results</translation>
    </message>
    <message>
        <source>需要处理</source>
        <translation>Needs attention</translation>
    </message>
    <message>
        <source>还没有历史试验</source>
        <translation>No experiments yet</translation>
    </message>
    <message>
        <source>完成第一次试验后，结果会显示在这里</source>
        <translation>Results appear here after your first experiment.</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
    <message>
        <source>仪器未连接</source>
        <translation>Instrument disconnected</translation>
    </message>
    <message>
        <source>正常</source>
        <translation>Normal</translation>
    </message>
    <message>
        <source>偏离范围</source>
        <translation>Out of range</translation>
    </message>
    <message>
        <source>要求 37 ± 1 ℃ · </source>
        <translation>Required 37 ± 1 ℃ · </translation>
    </message>
    <message>
        <source>仪器已连接</source>
        <translation>Instrument connected</translation>
    </message>
    <message>
        <source>当前没有进行中的试验。</source>
        <translation>No experiment is running.</translation>
    </message>
    <message>
        <source>{tubes} 管 · 已完成 {done} / {total} 排</source>
        <translation>{tubes} tubes · {done} / {total} rows done</translation>
    </message>
    <message>
        <source>{n} 管待复核</source>
        <translation>{n} to review</translation>
    </message>
    <message>
        <source>全部阴性</source>
        <translation>All negative</translation>
    </message>
    <message>
        <source>阳性 {p} · 阴性 {n}</source>
        <translation>{p} positive · {n} negative</translation>
    </message>
    <message>
        <source>试验 {id} 有 {n} 管待复核</source>
        <translation>Experiment {id}: {n} tubes to review</translation>
    </message>
    <message>
        <source>试验 {id} 处于异常状态，需要人工处理</source>
        <translation>Experiment {id} is faulted and needs manual attention.</translation>
    </message>
    <message>
        <source>{name} 已过期 {n} 天</source>
        <translation>{name} expired {n} days ago</translation>
    </message>
    <message>
        <source>{name} 还有 {n} 天过期</source>
        <translation>{name} expires in {n} days</translation>
    </message>
    <message>
        <source>没有需要处理的事项</source>
        <translation>Nothing needs attention.</translation>
    </message>
</context>
<context>
    <name>DataTable</name>
    <message>
        <source>暂无数据</source>
        <translation>No data</translation>
    </message>
    <message>
        <source>数据表格</source>
        <translation>Data table</translation>
    </message>
</context>
<context>
    <name>ErrorDialog</name>
    <message>
        <source>技术细节</source>
        <translation>Technical details</translation>
    </message>
    <message>
        <source>日志文件：</source>
        <translation>Log file: </translation>
    </message>
    <message>
        <source>关闭</source>
        <translation>Close</translation>
    </message>
</context>
<context>
    <name>ExperimentState</name>
    <message>
        <source>草稿</source>
        <translation>Draft</translation>
    </message>
    <message>
        <source>试剂校验中</source>
        <translation>Checking reagents</translation>
    </message>
    <message>
        <source>试剂已锁定</source>
        <translation>Reagents locked</translation>
    </message>
    <message>
        <source>就绪</source>
        <translation>Ready</translation>
    </message>
    <message>
        <source>孵育中</source>
        <translation>Incubating</translation>
    </message>
    <message>
        <source>读数中</source>
        <translation>Reading</translation>
    </message>
    <message>
        <source>分析中</source>
        <translation>Analyzing</translation>
    </message>
    <message>
        <source>待复核</source>
        <translation>Pending review</translation>
    </message>
    <message>
        <source>已完成</source>
        <translation>Completed</translation>
    </message>
    <message>
        <source>已签署</source>
        <translation>Signed</translation>
    </message>
    <message>
        <source>已停止</source>
        <translation>Stopped</translation>
    </message>
    <message>
        <source>异常</source>
        <translation>Faulted</translation>
    </message>
</context>
<context>
    <name>ExperimentType</name>
    <message>
        <source>灵敏度复核</source>
        <translation>Sensitivity confirmation</translation>
    </message>
    <message>
        <source>干扰试验</source>
        <translation>Interference test</translation>
    </message>
    <message>
        <source>半定量试验</source>
        <translation>Semi-quantitative test</translation>
    </message>
    <message>
        <source>限度试验</source>
        <translation>Limit test</translation>
    </message>
</context>
<context>
    <name>Export</name>
    <message>
        <source>试验</source>
        <translation>Experiment</translation>
    </message>
    <message>
        <source>单管结果</source>
        <translation>Tube results</translation>
    </message>
    <message>
        <source>结论依据</source>
        <translation>Conclusion basis</translation>
    </message>
    <message>
        <source>历史列表</source>
        <translation>History</translation>
    </message>
    <message>
        <source>审计追踪</source>
        <translation>Audit Trail</translation>
    </message>
    <message>
        <source>试验编号</source>
        <translation>Experiment ID</translation>
    </message>
    <message>
        <source>类型</source>
        <translation>Type</translation>
    </message>
    <message>
        <source>状态</source>
        <translation>Status</translation>
    </message>
    <message>
        <source>仪器编号</source>
        <translation>Instrument No.</translation>
    </message>
    <message>
        <source>操作人</source>
        <translation>Operator</translation>
    </message>
    <message>
        <source>创建时间</source>
        <translation>Created</translation>
    </message>
    <message>
        <source>完成时间</source>
        <translation>Completed</translation>
    </message>
    <message>
        <source>签署人</source>
        <translation>Signed by</translation>
    </message>
    <message>
        <source>签署时间</source>
        <translation>Signed at</translation>
    </message>
    <message>
        <source>鲎试剂批号</source>
        <translation>LAL lot</translation>
    </message>
    <message>
        <source>内毒素批号</source>
        <translation>Endotoxin standard lot</translation>
    </message>
    <message>
        <source>检测用水批号</source>
        <translation>BET water lot</translation>
    </message>
    <message>
        <source>标示灵敏度 λ（EU/mL）</source>
        <translation>Labeled sensitivity λ (EU/mL)</translation>
    </message>
    <message>
        <source>MVD</source>
        <translation>MVD</translation>
    </message>
    <message>
        <source>内毒素限值</source>
        <translation>Endotoxin limit</translation>
    </message>
    <message>
        <source>实测灵敏度 λc（EU/mL）</source>
        <translation>Measured sensitivity λc (EU/mL)</translation>
    </message>
    <message>
        <source>结论</source>
        <translation>Conclusion</translation>
    </message>
    <message>
        <source>结论数值</source>
        <translation>Conclusion value</translation>
    </message>
    <message>
        <source>规则版本</source>
        <translation>Rule version</translation>
    </message>
    <message>
        <source>管数</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>待复核管数</source>
        <translation>Tubes pending review</translation>
    </message>
    <message>
        <source>管号</source>
        <translation>Tube No.</translation>
    </message>
    <message>
        <source>排</source>
        <translation>Row</translation>
    </message>
    <message>
        <source>样品名称</source>
        <translation>Sample name</translation>
    </message>
    <message>
        <source>序列号</source>
        <translation>Serial No.</translation>
    </message>
    <message>
        <source>样品批号</source>
        <translation>Sample lot</translation>
    </message>
    <message>
        <source>稀释倍数</source>
        <translation>Dilution</translation>
    </message>
    <message>
        <source>外加内毒素（EU/mL）</source>
        <translation>Spiked endotoxin (EU/mL)</translation>
    </message>
    <message>
        <source>用途</source>
        <translation>Purpose</translation>
    </message>
    <message>
        <source>分析状态</source>
        <translation>Analysis status</translation>
    </message>
    <message>
        <source>算法三态</source>
        <translation>Gel state</translation>
    </message>
    <message>
        <source>自动判读</source>
        <translation>Automatic result</translation>
    </message>
    <message>
        <source>复核状态</source>
        <translation>Review status</translation>
    </message>
    <message>
        <source>最终判读</source>
        <translation>Final result</translation>
    </message>
    <message>
        <source>复核人</source>
        <translation>Reviewed by</translation>
    </message>
    <message>
        <source>复核时间</source>
        <translation>Reviewed at</translation>
    </message>
    <message>
        <source>复核备注</source>
        <translation>Review note</translation>
    </message>
    <message>
        <source>评分</source>
        <translation>Score</translation>
    </message>
    <message>
        <source>置信度</source>
        <translation>Confidence</translation>
    </message>
    <message>
        <source>面积变化率 ΔA（%）</source>
        <translation>Area change ΔA (%)</translation>
    </message>
    <message>
        <source>轴向偏移 Δs</source>
        <translation>Axial shift Δs</translation>
    </message>
    <message>
        <source>面积时序标准差 σ_A</source>
        <translation>Area std. dev. σ_A</translation>
    </message>
    <message>
        <source>轴向时序标准差 σ_s</source>
        <translation>Axial std. dev. σ_s</translation>
    </message>
    <message>
        <source>翻转前面积</source>
        <translation>Area before flip</translation>
    </message>
    <message>
        <source>翻转后面积</source>
        <translation>Area after flip</translation>
    </message>
    <message>
        <source>模型版本</source>
        <translation>Model version</translation>
    </message>
    <message>
        <source>配置版本</source>
        <translation>Config version</translation>
    </message>
    <message>
        <source>分析耗时（ms）</source>
        <translation>Analysis time (ms)</translation>
    </message>
    <message>
        <source>算法说明</source>
        <translation>Analyzer message</translation>
    </message>
    <message>
        <source>类别</source>
        <translation>Category</translation>
    </message>
    <message>
        <source>内容</source>
        <translation>Content</translation>
    </message>
    <message>
        <source>依据</source>
        <translation>Basis</translation>
    </message>
    <message>
        <source>未满足的前提</source>
        <translation>Unmet preconditions</translation>
    </message>
    <message>
        <source>序号</source>
        <translation>No.</translation>
    </message>
    <message>
        <source>时间</source>
        <translation>Time</translation>
    </message>
    <message>
        <source>操作者</source>
        <translation>Actor</translation>
    </message>
    <message>
        <source>权限</source>
        <translation>Permission</translation>
    </message>
    <message>
        <source>事件类型</source>
        <translation>Event type</translation>
    </message>
    <message>
        <source>操作记录</source>
        <translation>Action</translation>
    </message>
    <message>
        <source>关联试验</source>
        <translation>Related experiment</translation>
    </message>
    <message>
        <source>哈希</source>
        <translation>Hash</translation>
    </message>
</context>
<context>
    <name>ExportPasswordDialog</name>
    <message>
        <source>导出密码至少 {min} 个字符</source>
        <translation>The export password must have at least {min} characters</translation>
    </message>
    <message>
        <source>两次输入的密码不一致</source>
        <translation>The passwords do not match.</translation>
    </message>
    <message>
        <source>设置导出密码</source>
        <translation>Set export password</translation>
    </message>
    <message>
        <source>已开启导出加密：文件将打成 AES-256 加密的 ZIP，打开时需要这个密码。</source>
        <translation>Export encryption is on: the file is packed into an AES-256 encrypted ZIP and needs this password to open.</translation>
    </message>
    <message>
        <source>导出密码</source>
        <translation>Export password</translation>
    </message>
    <message>
        <source>再输入一次</source>
        <translation>Repeat password</translation>
    </message>
    <message>
        <source>软件不保存这个密码，忘记后无法找回。请用 7-Zip、Keka 等工具解压，系统自带的解压不支持 AES 加密。</source>
        <translation>The password is not stored and cannot be recovered. Extract with 7-Zip, Keka or similar; the built-in system tools do not support AES.</translation>
    </message>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>导出</source>
        <translation>Export</translation>
    </message>
</context>
<context>
    <name>ExportValue</name>
    <message>
        <source>阳性</source>
        <translation>Positive</translation>
    </message>
    <message>
        <source>阴性</source>
        <translation>Negative</translation>
    </message>
    <message>
        <source>待复核</source>
        <translation>Pending review</translation>
    </message>
    <message>
        <source>完全凝固</source>
        <translation>Fully gelled</translation>
    </message>
    <message>
        <source>部分凝固</source>
        <translation>Partially gelled</translation>
    </message>
    <message>
        <source>未凝固</source>
        <translation>Not gelled</translation>
    </message>
    <message>
        <source>无需复核</source>
        <translation>No review needed</translation>
    </message>
    <message>
        <source>等待复核</source>
        <translation>Awaiting review</translation>
    </message>
    <message>
        <source>已复核</source>
        <translation>Reviewed</translation>
    </message>
    <message>
        <source>分析成功</source>
        <translation>Analyzed</translation>
    </message>
    <message>
        <source>凝胶缺失</source>
        <translation>Gel missing</translation>
    </message>
    <message>
        <source>未找到试管</source>
        <translation>Tube not found</translation>
    </message>
    <message>
        <source>图像质量不足</source>
        <translation>Low image quality</translation>
    </message>
    <message>
        <source>分析失败</source>
        <translation>Analysis failed</translation>
    </message>
</context>
<context>
    <name>GelState</name>
    <message>
        <source>完全凝固</source>
        <translation>Fully gelled</translation>
    </message>
    <message>
        <source>部分凝固</source>
        <translation>Partially gelled</translation>
    </message>
    <message>
        <source>未凝固</source>
        <translation>Not gelled</translation>
    </message>
    <message>
        <source>无法判定</source>
        <translation>Undetermined</translation>
    </message>
</context>
<context>
    <name>HistoryPage</name>
    <message>
        <source>当前角色无此权限</source>
        <translation>Your role does not have this permission</translation>
    </message>
    <message>
        <source>先在列表里点选要导出的试验</source>
        <translation>Click experiments in the list first</translation>
    </message>
    <message>
        <source>导出所选试验结果</source>
        <translation>Export selected experiment results</translation>
    </message>
    <message>
        <source>Excel 文件 (*.xlsx)</source>
        <translation>Excel files (*.xlsx)</translation>
    </message>
    <message>
        <source>导出历史列表</source>
        <translation>Export history</translation>
    </message>
    <message>
        <source>已导出 {rows} 行：{name}</source>
        <translation>Exported {rows} rows: {name}</translation>
    </message>
    <message>
        <source>导出失败：{reason}</source>
        <translation>Export failed: {reason}</translation>
    </message>
    <message>
        <source>试验</source>
        <translation>Experiment</translation>
    </message>
    <message>
        <source>类型</source>
        <translation>Type</translation>
    </message>
    <message>
        <source>试管</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>状态</source>
        <translation>Status</translation>
    </message>
    <message>
        <source>操作人</source>
        <translation>Operator</translation>
    </message>
    <message>
        <source>创建时间</source>
        <translation>Created</translation>
    </message>
    <message>
        <source>完成时间</source>
        <translation>Completed</translation>
    </message>
    <message>
        <source>签署时间</source>
        <translation>Signed at</translation>
    </message>
    <message>
        <source>全部类型</source>
        <translation>All types</translation>
    </message>
    <message>
        <source>全部状态</source>
        <translation>All statuses</translation>
    </message>
    <message>
        <source>历史记录</source>
        <translation>History</translation>
    </message>
    <message>
        <source>双击一行打开回看，点选多行可以导出</source>
        <translation>Double-click a row to open it; click several rows to export them</translation>
    </message>
    <message>
        <source>导出所选结果…</source>
        <translation>Export selected results…</translation>
    </message>
    <message>
        <source>导出当前列表…</source>
        <translation>Export current list…</translation>
    </message>
    <message>
        <source>试验类型</source>
        <translation>Test type</translation>
    </message>
    <message>
        <source>开始日期</source>
        <translation>From date</translation>
    </message>
    <message>
        <source>结束日期</source>
        <translation>To date</translation>
    </message>
    <message>
        <source>批号</source>
        <translation>Lot No.</translation>
    </message>
    <message>
        <source>鲎试剂或样品</source>
        <translation>LAL or sample</translation>
    </message>
    <message>
        <source>查询</source>
        <translation>Search</translation>
    </message>
    <message>
        <source>重置</source>
        <translation>Reset</translation>
    </message>
    <message>
        <source>刷新</source>
        <translation>Refresh</translation>
    </message>
    <message>
        <source>打开所选试验</source>
        <translation>Open selected</translation>
    </message>
    <message>
        <source>还没有历史试验</source>
        <translation>No experiments yet</translation>
    </message>
    <message>
        <source>全部操作人</source>
        <translation>All operators</translation>
    </message>
    <message>
        <source>没有符合条件的试验</source>
        <translation>No experiments match</translation>
    </message>
    <message>
        <source>调整筛选条件，或点“重置”清除条件后再查</source>
        <translation>Adjust the filters, or click Reset to clear them and search again</translation>
    </message>
    <message>
        <source>完成第一次试验后，结果会显示在这里</source>
        <translation>Results appear here after the first experiment</translation>
    </message>
</context>
<context>
    <name>ImagesTab</name>
    <message>
        <source>没有选中的试验</source>
        <translation>No experiment selected</translation>
    </message>
    <message>
        <source>这个试验还没有抓拍图像，每排读数后会显示在这里</source>
        <translation>No captured images yet; they appear here after each row is read</translation>
    </message>
    <message>
        <source>图像显示</source>
        <translation>Images</translation>
    </message>
</context>
<context>
    <name>Interpretation</name>
    <message>
        <source>阳性</source>
        <translation>Positive</translation>
    </message>
    <message>
        <source>阴性</source>
        <translation>Negative</translation>
    </message>
    <message>
        <source>待复核</source>
        <translation>Pending review</translation>
    </message>
</context>
<context>
    <name>JudgmentGuideDialog</name>
    <message>
        <source>判读依据</source>
        <translation>Reading criteria</translation>
    </message>
    <message>
        <source>凝胶法判读标准</source>
        <translation>Gel-clot reading criterion</translation>
    </message>
    <message>
        <source>按中国药典通则 1143 凝胶法：保温 60±2 分钟后，将试管轻轻取出，缓缓倒转 180°。管内形成凝胶，并且凝胶不变形、不从管壁滑脱的，为阳性；未形成凝胶，或形成的凝胶不能保持完整并从管壁滑脱的，为阴性。</source>
        <translation>Per Chinese Pharmacopoeia General Chapter 1143 (gel-clot method): after incubating for 60 ± 2 minutes, take the tube out gently and invert it slowly through 180°. If a gel has formed that keeps its shape and does not slip down the tube wall, the result is positive; if no gel has formed, or the gel cannot stay intact and slips from the wall, the result is negative.</translation>
    </message>
    <message>
        <source>示例：形成凝胶，倒置后不脱落（阳性）</source>
        <translation>Example: gel formed and stays in place when inverted (positive)</translation>
    </message>
    <message>
        <source>示例：未形成凝胶（阴性）</source>
        <translation>Example: no gel formed (negative)</translation>
    </message>
    <message>
        <source>示例照片只用于说明，不参与判读。</source>
        <translation>The example photos are for illustration only and are not used for reading.</translation>
    </message>
    <message>
        <source>软件的判读步骤</source>
        <translation>How the software reads a tube</translation>
    </message>
    <message>
        <source>取图：每排翻转前、翻转后各取至少 3 帧图像。</source>
        <translation>Capture: take at least 3 frames of each row before and after inversion.</translation>
    </message>
    <message>
        <source>分割：在图像中识别每支试管的试管区域和凝胶区域。</source>
        <translation>Segmentation: find the tube region and the gel region of each tube in the images.</translation>
    </message>
    <message>
        <source>特征：计算面积变化率 ΔA、试管坐标系下的质心偏移 Δs，以及多帧之间的面积波动 σ_A 和位置波动 σ_s。</source>
        <translation>Features: compute the area change ΔA, the centroid shift Δs in tube coordinates, and the frame-to-frame area variation σ_A and position variation σ_s.</translation>
    </message>
    <message>
        <source>评分：每项特征归一化到 0–1（越大越像凝胶），按权重加权得到融合评分 Score。</source>
        <translation>Score: normalize each feature to 0–1 (higher means more gel-like) and combine them with weights into the fused score.</translation>
    </message>
    <message>
        <source>三态：Score 不低于完全凝固阈值为完全凝固，不高于未凝固阈值为未凝固，其余为部分凝固。</source>
        <translation>Gel state: a score at or above the fully-gelled threshold means fully gelled, at or below the not-gelled threshold means not gelled, and anything in between means partially gelled.</translation>
    </message>
    <message>
        <source>阴阳性：按下表换算；图像质量差、找不到试管、分析失败的试管一律待人工复核，不会默认判为阴性。</source>
        <translation>Result: converted with the table below. Tubes with poor images, no tube found or a failed analysis always go to manual review and are never assumed negative.</translation>
    </message>
    <message>
        <source>凝固状态与阴阳性（当前配置）</source>
        <translation>Gel state and result (current configuration)</translation>
    </message>
    <message>
        <source>置信度低于 {t} 的结果也转人工复核。</source>
        <translation>Results with confidence below {t} also go to manual review.</translation>
    </message>
    <message>
        <source>关闭</source>
        <translation>Close</translation>
    </message>
</context>
<context>
    <name>LimitField</name>
    <message>
        <source>MVD</source>
        <translation>MVD</translation>
    </message>
    <message>
        <source>内毒素限值</source>
        <translation>Endotoxin limit</translation>
    </message>
</context>
<context>
    <name>LoginPage</name>
    <message>
        <source>请输入用户名</source>
        <translation>Enter your username.</translation>
    </message>
    <message>
        <source>请输入密码</source>
        <translation>Enter your password.</translation>
    </message>
    <message>
        <source>失败次数过多，已暂时锁定，{time} 后可再试</source>
        <translation>Too many failed attempts. Locked, try again in {time}.</translation>
    </message>
    <message>
        <source>用户名</source>
        <translation>Username</translation>
    </message>
    <message>
        <source>密码</source>
        <translation>Password</translation>
    </message>
    <message>
        <source>屏幕已锁定</source>
        <translation>Screen locked</translation>
    </message>
    <message>
        <source>解锁</source>
        <translation>Unlock</translation>
    </message>
    <message>
        <source>退出登录</source>
        <translation>Sign out</translation>
    </message>
    <message>
        <source>输入密码解锁，试验仍在后台运行</source>
        <translation>Enter password to unlock; experiments keep running</translation>
    </message>
    <message>
        <source>登录</source>
        <translation>Sign in</translation>
    </message>
    <message>
        <source>演示账号（模拟配置，点击填入）</source>
        <translation>Demo accounts (simulation, click to fill in)</translation>
    </message>
</context>
<context>
    <name>MainWindow</name>
    <message>
        <source>当前角色无此权限：{page}</source>
        <translation>Your role cannot open: {page}</translation>
    </message>
    <message>
        <source>密码已修改</source>
        <translation>Password changed</translation>
    </message>
    <message>
        <source>凝胶智检系统 GelInspect</source>
        <translation>GelInspect</translation>
    </message>
    <message>
        <source>有试验正在进行，请先解锁再退出</source>
        <translation>An experiment is running. Unlock before quitting.</translation>
    </message>
    <message>
        <source>退出并中断试验？</source>
        <translation>Quit and interrupt the experiment?</translation>
    </message>
    <message>
        <source>试验 {id} 仍在进行。退出会中断它并标记为异常，设备状态需要人工检查，未读数的排会错过读数时间。确定要退出吗？</source>
        <translation>Experiment {id} is still running. Quitting interrupts it and marks it faulted; the device state must be checked manually and unread rows will miss their reading time. Quit anyway?</translation>
    </message>
    <message>
        <source>中断并退出</source>
        <translation>Interrupt and quit</translation>
    </message>
    <message>
        <source>继续试验</source>
        <translation>Keep running</translation>
    </message>
</context>
<context>
    <name>MediaWarning</name>
    <message>
        <source>图像与入库的 SHA-256 不符，可能已被改动</source>
        <translation>Image does not match its stored SHA-256 and may have been altered</translation>
    </message>
    <message>
        <source>部分图像文件缺失或无法解码</source>
        <translation>Some image files are missing or cannot be decoded</translation>
    </message>
    <message>
        <source>没有存档的掩码，按孔位切分显示</source>
        <translation>No archived masks; cropped by well position</translation>
    </message>
</context>
<context>
    <name>MiniPlate</name>
    <message>
        <source>孔板缩略图：</source>
        <translation>Plate overview: </translation>
    </message>
    <message>
        <source>、</source>
        <translation>, </translation>
    </message>
</context>
<context>
    <name>MonitorPage</name>
    <message>
        <source>第 {n} 排读数中</source>
        <translation>Row {n} reading</translation>
    </message>
    <message>
        <source>第 {n} 排分析中</source>
        <translation>Row {n} analyzing</translation>
    </message>
    <message>
        <source>待复核 · {n} 管</source>
        <translation>Pending review · {n} tubes</translation>
    </message>
    <message>
        <source>先选择一个试验。</source>
        <translation>Select an experiment first.</translation>
    </message>
    <message>
        <source>这个试验已签署，可以预览、打印、验签和导出已签名的 PDF。</source>
        <translation>This experiment is signed. You can preview, print, verify and export the signed PDF.</translation>
    </message>
    <message>
        <source>这个试验已完成，可以预览和打印报告；分析员核对后签署。</source>
        <translation>This experiment is completed. You can preview and print the report; an analyst signs it after checking.</translation>
    </message>
    <message>
        <source>这个试验还没有完成，可以先预览报告；完成并复核后才能签署。</source>
        <translation>This experiment is not completed yet. You can preview the report; it can be signed after completion and review.</translation>
    </message>
    <message>
        <source>把同一批号的多次试验汇总到一份报告。选择本试验用到的批号：</source>
        <translation>Summarize all experiments of one lot in a single report. Choose a lot used by this experiment:</translation>
    </message>
    <message>
        <source>这个试验没有批号可以汇总。</source>
        <translation>This experiment has no lot number to summarize.</translation>
    </message>
    <message>
        <source>鲎试剂批号</source>
        <translation>LAL reagent lot</translation>
    </message>
    <message>
        <source>样品批号</source>
        <translation>Sample lot</translation>
    </message>
    <message>
        <source>试管编号</source>
        <translation>Tube</translation>
    </message>
    <message>
        <source>样品名称</source>
        <translation>Sample</translation>
    </message>
    <message>
        <source>序列号</source>
        <translation>Serial no.</translation>
    </message>
    <message>
        <source>批号</source>
        <translation>Lot</translation>
    </message>
    <message>
        <source>稀释倍数</source>
        <translation>Dilution</translation>
    </message>
    <message>
        <source>外加内毒素</source>
        <translation>Spiked endotoxin</translation>
    </message>
    <message>
        <source>阴阳性</source>
        <translation>Result</translation>
    </message>
    <message>
        <source>试验 {id}</source>
        <translation>Experiment {id}</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for this role</translation>
    </message>
    <message>
        <source>全部排读完后才能重试分析</source>
        <translation>Analysis can be retried after all rows are read</translation>
    </message>
    <message>
        <source>软件重启过，调度已经丢失，这个试验不能恢复。第 {rows} 排已抓帧，可以重试分析（不翻转）；第 {unread} 排错过了读数时间，所以分析后试验仍只能停止。</source>
        <translation>The software restarted and the schedule is lost, so this experiment cannot resume. Rows {rows} were captured and can be re-analyzed (no flipping); rows {unread} missed their read time, so after analysis the experiment can only be stopped.</translation>
    </message>
    <message>
        <source>软件重启过，调度已经丢失，这个试验不能恢复。全部排都已抓帧：第 {rows} 排可以重试分析（不翻转），完成后试验照常结束。</source>
        <translation>The software restarted and the schedule is lost, so this experiment cannot resume. All rows were captured: rows {rows} can be re-analyzed (no flipping), and the experiment then finishes normally.</translation>
    </message>
    <message>
        <source>软件重启过，调度已经丢失，这个试验不能恢复。没有可以重试分析的排（没有抓帧，或抓帧记录缺少抓帧时间），只能停止试验。</source>
        <translation>The software restarted and the schedule is lost, so this experiment cannot resume. No row can be re-analyzed (not captured, or the capture time was not recorded). It can only be stopped.</translation>
    </message>
    <message>
        <source>第 {rows} 排分析失败，原图已保留。全部排读完后可以重试分析。</source>
        <translation>Analysis failed for rows {rows}; the original images are kept. It can be retried after all rows are read.</translation>
    </message>
    <message>
        <source>试验异常：{reason}</source>
        <translation>Experiment fault: {reason}</translation>
    </message>
    <message>
        <source>回放第 {n} 排</source>
        <translation>Replay row {n}</translation>
    </message>
    <message>
        <source>回放（还没有录像）</source>
        <translation>Replay (no recording)</translation>
    </message>
    <message>
        <source>等待视频画面…</source>
        <translation>Waiting for video…</translation>
    </message>
    <message>
        <source>翻转录制中</source>
        <translation>Recording flip</translation>
    </message>
    <message>
        <source>翻转中</source>
        <translation>Flipping</translation>
    </message>
    <message>
        <source>算法判读中</source>
        <translation>Analyzing</translation>
    </message>
    <message>
        <source>翻转后</source>
        <translation>After flip</translation>
    </message>
    <message>
        <source>孵育中</source>
        <translation>Incubating</translation>
    </message>
    <message>
        <source>待机</source>
        <translation>Standby</translation>
    </message>
    <message>
        <source>算法判读 {elapsed} · 翻转角度 {angle}</source>
        <translation>Analysis {elapsed} · Flip angle {angle}</translation>
    </message>
    <message>
        <source>停止试验？</source>
        <translation>Stop the experiment?</translation>
    </message>
    <message>
        <source>停止后试验 {id} 不能继续，还没读数的排会错过读数时间；已经翻转的排会复位。确定要停止吗？</source>
        <translation>Experiment {id} cannot continue after stopping; rows not yet read will miss their reading time, and flipped rows will be reset. Stop it?</translation>
    </message>
    <message>
        <source>停止试验</source>
        <translation>Stop experiment</translation>
    </message>
    <message>
        <source>继续试验</source>
        <translation>Keep running</translation>
    </message>
    <message>
        <source>已请求停止，正在收尾</source>
        <translation>Stop requested, finishing up</translation>
    </message>
    <message>
        <source>恢复试验？</source>
        <translation>Resume the experiment?</translation>
    </message>
    <message>
        <source>恢复前请确认设备和温度已正常。读数失败的排会重新翻转，翻动过的凝胶状态可能已变；分析失败的排用保留的原图重试。确定要恢复吗？</source>
        <translation>Check that the instrument and temperature are back to normal. Rows whose read failed will be flipped again, and gel that has been moved may have changed; rows whose analysis failed are retried from the kept images. Resume?</translation>
    </message>
    <message>
        <source>恢复试验</source>
        <translation>Resume experiment</translation>
    </message>
    <message>
        <source>暂不恢复</source>
        <translation>Not now</translation>
    </message>
    <message>
        <source>已请求恢复试验</source>
        <translation>Resume requested</translation>
    </message>
    <message>
        <source>已请求重试分析</source>
        <translation>Analysis retry requested</translation>
    </message>
    <message>
        <source>试验数据记录</source>
        <translation>Experiment data</translation>
    </message>
    <message>
        <source>图像显示</source>
        <translation>Images</translation>
    </message>
    <message>
        <source>报告打印预览</source>
        <translation>Report preview</translation>
    </message>
    <message>
        <source>单批次报告</source>
        <translation>Lot report</translation>
    </message>
    <message>
        <source>当前没有进行中的试验</source>
        <translation>No experiment is running</translation>
    </message>
    <message>
        <source>在“新试验”里启动试验后，这里显示实时画面、孔板和结果；也可以到历史记录回看已完成的试验。</source>
        <translation>Start one from New experiment to see live video, the plate and results here, or open History to review finished experiments.</translation>
    </message>
    <message>
        <source>打开历史记录</source>
        <translation>Open history</translation>
    </message>
    <message>
        <source>重试分析</source>
        <translation>Retry analysis</translation>
    </message>
    <message>
        <source>{kind}批号</source>
        <translation>{kind} lot</translation>
    </message>
    <message>
        <source>标示灵敏度 λ</source>
        <translation>Labeled sensitivity λ</translation>
    </message>
    <message>
        <source>实测灵敏度 λc</source>
        <translation>Measured sensitivity λc</translation>
    </message>
    <message>
        <source>操作人</source>
        <translation>Operator</translation>
    </message>
    <message>
        <source>开始时间</source>
        <translation>Start time</translation>
    </message>
    <message>
        <source>实时视频</source>
        <translation>Live video</translation>
    </message>
    <message>
        <source>按排回放</source>
        <translation>Row replay</translation>
    </message>
    <message>
        <source>这个试验没有录像</source>
        <translation>This experiment has no recordings</translation>
    </message>
    <message>
        <source>没有试管</source>
        <translation>No tubes</translation>
    </message>
    <message>
        <source>在报告中心打开</source>
        <translation>Open in Report Center</translation>
    </message>
</context>
<context>
    <name>NewExperimentPage</name>
    <message>
        <source>序号</source>
        <translation>No.</translation>
    </message>
    <message>
        <source>样品名称</source>
        <translation>Sample name</translation>
    </message>
    <message>
        <source>样品批号</source>
        <translation>Sample lot</translation>
    </message>
    <message>
        <source>点击录入</source>
        <translation>Click to enter</translation>
    </message>
    <message>
        <source>外加内毒素</source>
        <translation>Spiked endotoxin</translation>
    </message>
    <message>
        <source>管号</source>
        <translation>Tube</translation>
    </message>
    <message>
        <source>排</source>
        <translation>Row</translation>
    </message>
    <message>
        <source>新试验</source>
        <translation>New experiment</translation>
    </message>
    <message>
        <source>选择模板</source>
        <translation>Template</translation>
    </message>
    <message>
        <source>校验试剂</source>
        <translation>Reagents</translation>
    </message>
    <message>
        <source>录入样品和管位</source>
        <translation>Samples &amp; tubes</translation>
    </message>
    <message>
        <source>确认启动</source>
        <translation>Confirm</translation>
    </message>
    <message>
        <source>选择检测模板</source>
        <translation>Choose a test template</translation>
    </message>
    <message>
        <source>模板决定试管数量和每管的用途、样品、稀释倍数和外加内毒素。</source>
        <translation>The template sets the number of tubes and each tube&apos;s role, sample, dilution and spiked endotoxin.</translation>
    </message>
    <message>
        <source>还没有可用的模板</source>
        <translation>No templates available yet</translation>
    </message>
    <message>
        <source>下一步：校验试剂</source>
        <translation>Next: verify reagents</translation>
    </message>
    <message>
        <source>用扫码枪扫试剂上的码（扫完自动回车），或手输后按回车。三种试剂都合格才能继续。</source>
        <translation>Scan the code on each reagent (the scanner presses Enter itself), or type it and press Enter. All three reagents must pass to continue.</translation>
    </message>
    <message>
        <source>上一步</source>
        <translation>Back</translation>
    </message>
    <message>
        <source>下一步：录入样品和管位</source>
        <translation>Next: samples &amp; tubes</translation>
    </message>
    <message>
        <source>模板</source>
        <translation>Template</translation>
    </message>
    <message>
        <source>更换模板</source>
        <translation>Change template</translation>
    </message>
    <message>
        <source>样品</source>
        <translation>Samples</translation>
    </message>
    <message>
        <source>MVD（可选）</source>
        <translation>MVD (optional)</translation>
    </message>
    <message>
        <source>内毒素限值 EU/mL（可选）</source>
        <translation>Endotoxin limit, EU/mL (optional)</translation>
    </message>
    <message>
        <source>留空则报告里不填</source>
        <translation>Leave empty to leave it blank in the report</translation>
    </message>
    <message>
        <source>录入后写入试验记录并显示在报告里；试验签署后不能再修改。</source>
        <translation>Saved with the experiment record and shown in the report; it cannot be changed after signing.</translation>
    </message>
    <message>
        <source>管位</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>{rows} 排 × {holes} 支管位</source>
        <translation>{rows} rows × {holes} tubes</translation>
    </message>
    <message>
        <source>自动选择</source>
        <translation>Auto-select</translation>
    </message>
    <message>
        <source>全部清除</source>
        <translation>Clear all</translation>
    </message>
    <message>
        <source>下一步：确认启动</source>
        <translation>Next: confirm</translation>
    </message>
    <message>
        <source>试验信息</source>
        <translation>Experiment details</translation>
    </message>
    <message>
        <source>确认后设备会连接并送入滑台，随后开始孵育。启动前会检查温度、舱门和视频。</source>
        <translation>After you confirm, the instrument connects, moves the tray in and starts incubation. Temperature, door and video are checked first.</translation>
    </message>
    <message>
        <source>样品与管位</source>
        <translation>Samples and tubes</translation>
    </message>
    <message>
        <source>没有样品</source>
        <translation>No samples</translation>
    </message>
    <message>
        <source>离开新试验向导？</source>
        <translation>Leave the new experiment wizard?</translation>
    </message>
    <message>
        <source>向导里已录入的内容还没有提交，离开后会丢失。确定要离开吗？</source>
        <translation>What you entered has not been submitted and will be lost. Leave anyway?</translation>
    </message>
    <message>
        <source>离开</source>
        <translation>Leave</translation>
    </message>
    <message>
        <source>留在向导</source>
        <translation>Stay</translation>
    </message>
    <message>
        <source>更换模板？</source>
        <translation>Change template?</translation>
    </message>
    <message>
        <source>更换模板会清空已录入的样品和已选的管位。确定要更换吗？</source>
        <translation>Changing the template clears the samples you entered and the tubes you selected. Change it anyway?</translation>
    </message>
    <message>
        <source>更换</source>
        <translation>Change</translation>
    </message>
    <message>
        <source>保留当前模板</source>
        <translation>Keep current</translation>
    </message>
    <message>
        <source>试验流程已锁定，不能进入下一步。请更换合格的试剂批次后重新扫码。本次校验已记入审计。</source>
        <translation>The workflow is locked and cannot continue. Replace the reagent with a valid lot and scan again. This check was written to the audit trail.</translation>
    </message>
    <message>
        <source>试剂未通过校验</source>
        <translation>Reagent check failed</translation>
    </message>
    <message>
        <source>缺少标示灵敏度 λ，不能进入下一步</source>
        <translation>Labeled sensitivity λ is missing, so you cannot continue</translation>
    </message>
    <message>
        <source>这个模板按 λ 计算外加内毒素。请换一个有标示灵敏度的鲎试剂批次，或把灵敏度补录到备案信息后重新扫码。</source>
        <translation>This template calculates spiked endotoxin from λ. Use an LAL reagent lot with a labeled sensitivity, or add it to the registration data and scan again.</translation>
    </message>
    <message>
        <source>试剂未通过校验，流程已锁定，不能进入下一步</source>
        <translation>Reagent check failed. The workflow is locked and cannot continue.</translation>
    </message>
    <message>
        <source>请更换合格的试剂批次后重新扫码。本次校验已记入审计。</source>
        <translation>Replace it with a valid lot and scan again. This check was written to the audit trail.</translation>
    </message>
    <message>
        <source> · 标示灵敏度 {value:g} EU/mL</source>
        <translation> · labeled sensitivity {value:g} EU/mL</translation>
    </message>
    <message>
        <source> · {count} 管</source>
        <translation> · {count} tubes</translation>
    </message>
    <message>
        <source>已选 {picked} / {total} ·</source>
        <translation>Selected {picked} / {total} ·</translation>
    </message>
    <message>
        <source>请先选择模板</source>
        <translation>Select a template first</translation>
    </message>
    <message>
        <source>请在孔板上选择管位</source>
        <translation>Select tubes on the plate</translation>
    </message>
    <message>
        <source>管位已对应</source>
        <translation>Tubes match</translation>
    </message>
    <message>
        <source>还差 {n} 个</source>
        <translation>{n} more needed</translation>
    </message>
    <message>
        <source>多选了 {n} 个</source>
        <translation>{n} too many</translation>
    </message>
    <message>
        <source>第 {rows} 行的样品名称为空，请填写</source>
        <translation>Sample name is empty in row {rows}</translation>
    </message>
    <message>
        <source>MVD</source>
        <translation>MVD</translation>
    </message>
    <message>
        <source>内毒素限值</source>
        <translation>Endotoxin limit</translation>
    </message>
    <message>
        <source>“{names}”必须是大于 0 的数字，或者留空</source>
        <translation>&quot;{names}&quot; must be a number greater than 0, or empty</translation>
    </message>
    <message>
        <source>、</source>
        <translation>, </translation>
    </message>
    <message>
        <source>管位数量要与模板一致，样品名称不能为空</source>
        <translation>Tube count must match the template and sample names cannot be empty</translation>
    </message>
    <message>
        <source>试验类型</source>
        <translation>Experiment type</translation>
    </message>
    <message>
        <source>试管</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>{count} 管 · {rows}</source>
        <translation>{count} tubes · {rows}</translation>
    </message>
    <message>
        <source>孵育时长</source>
        <translation>Incubation</translation>
    </message>
    <message>
        <source>{minutes:g} 分钟</source>
        <translation>{minutes:g} min</translation>
    </message>
    <message>
        <source>操作人</source>
        <translation>Operator</translation>
    </message>
    <message>
        <source>{batch} · 效期至 {date}</source>
        <translation>{batch} · expires {date}</translation>
    </message>
    <message>
        <source>正在启动</source>
        <translation>Starting</translation>
    </message>
    <message>
        <source>正在连接设备、检查温度和视频，请稍候……</source>
        <translation>Connecting to the instrument and checking temperature and video…</translation>
    </message>
    <message>
        <source>无法启动</source>
        <translation>Cannot start</translation>
    </message>
    <message>
        <source>启动失败，试验仍为就绪，可以重试</source>
        <translation>Start failed. The experiment is still ready; you can retry.</translation>
    </message>
    <message>
        <source>原因：</source>
        <translation>Reason: </translation>
    </message>
    <message>
        <source>重试启动</source>
        <translation>Retry start</translation>
    </message>
    <message>
        <source>确认并启动</source>
        <translation>Confirm and start</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
    <message>
        <source>这个模板需要鲎试剂的标示灵敏度 λ</source>
        <translation>This template needs the labeled sensitivity λ of the LAL reagent</translation>
    </message>
</context>
<context>
    <name>NewUserDialog</name>
    <message>
        <source>新建用户</source>
        <translation>New user</translation>
    </message>
    <message>
        <source>登录名</source>
        <translation>Login name</translation>
    </message>
    <message>
        <source>显示名称</source>
        <translation>Display name</translation>
    </message>
    <message>
        <source>角色</source>
        <translation>Role</translation>
    </message>
    <message>
        <source>初始密码（可选）</source>
        <translation>Initial password (optional)</translation>
    </message>
    <message>
        <source>留空则自动生成临时密码</source>
        <translation>Leave empty to generate a temporary password</translation>
    </message>
    <message>
        <source>新账号首次登录必须先修改密码。</source>
        <translation>The new account must change its password at first sign-in.</translation>
    </message>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>创建</source>
        <translation>Create</translation>
    </message>
</context>
<context>
    <name>NoticeDialog</name>
    <message>
        <source>知道了</source>
        <translation>OK</translation>
    </message>
</context>
<context>
    <name>Pager</name>
    <message>
        <source>共 {total} 条 · 第 {page}/{pages} 页</source>
        <translation>{total} total · page {page}/{pages}</translation>
    </message>
    <message>
        <source>上一页</source>
        <translation>Previous</translation>
    </message>
    <message>
        <source>下一页</source>
        <translation>Next</translation>
    </message>
</context>
<context>
    <name>Permission</name>
    <message>
        <source>管理用户和角色</source>
        <translation>Manage users and roles</translation>
    </message>
    <message>
        <source>修改仪器设置</source>
        <translation>Change instrument settings</translation>
    </message>
    <message>
        <source>查看审计追踪</source>
        <translation>View audit trail</translation>
    </message>
    <message>
        <source>查看检测模板</source>
        <translation>View test templates</translation>
    </message>
    <message>
        <source>新建和修改检测模板</source>
        <translation>Create and edit test templates</translation>
    </message>
    <message>
        <source>录入和校验试剂批次</source>
        <translation>Enter and verify reagent lots</translation>
    </message>
    <message>
        <source>查看试验和结果</source>
        <translation>View experiments and results</translation>
    </message>
    <message>
        <source>新建试验</source>
        <translation>New experiment</translation>
    </message>
    <message>
        <source>启动试验</source>
        <translation>Start experiments</translation>
    </message>
    <message>
        <source>停止试验</source>
        <translation>Stop experiments</translation>
    </message>
    <message>
        <source>人工复核单管结果</source>
        <translation>Review tube results manually</translation>
    </message>
    <message>
        <source>签署试验和报告</source>
        <translation>Sign experiments and reports</translation>
    </message>
    <message>
        <source>预览和打印报告</source>
        <translation>Preview and print reports</translation>
    </message>
    <message>
        <source>导出报告和数据</source>
        <translation>Export reports and data</translation>
    </message>
</context>
<context>
    <name>PinButton</name>
    <message>
        <source>取消固定侧栏</source>
        <translation>Unpin sidebar</translation>
    </message>
    <message>
        <source>固定侧栏</source>
        <translation>Pin sidebar</translation>
    </message>
</context>
<context>
    <name>PlateGrid</name>
    <message>
        <source>孔板 {rows} 排 × {holes} 支</source>
        <translation>Plate {rows} × {holes}</translation>
    </message>
    <message>
        <source>{no} 号管，{state}</source>
        <translation>Tube {no}, {state}</translation>
    </message>
    <message>
        <source>，已选中</source>
        <translation>, selected</translation>
    </message>
    <message>
        <source>已读数</source>
        <translation>Done</translation>
    </message>
    <message>
        <source>读数中</source>
        <translation>Reading</translation>
    </message>
    <message>
        <source>分析中</source>
        <translation>Analyzing</translation>
    </message>
    <message>
        <source>读数失败</source>
        <translation>Read failed</translation>
    </message>
    <message>
        <source>分析失败</source>
        <translation>Analysis failed</translation>
    </message>
</context>
<context>
    <name>PlateLegend</name>
    <message>
        <source>图例：</source>
        <translation>Legend: </translation>
    </message>
    <message>
        <source>、</source>
        <translation>, </translation>
    </message>
</context>
<context>
    <name>Punctuation</name>
    <message>
        <source>{text}（{note}）</source>
        <translation>{text} ({note})</translation>
    </message>
    <message>
        <source>{text}：{note}</source>
        <translation>{text}: {note}</translation>
    </message>
    <message>
        <source>、</source>
        <translation>, </translation>
    </message>
</context>
<context>
    <name>QuickToolbar</name>
    <message>
        <source>新建试验</source>
        <translation>New experiment</translation>
    </message>
    <message>
        <source>打开文件</source>
        <translation>Open file</translation>
    </message>
    <message>
        <source>检测模板</source>
        <translation>Templates</translation>
    </message>
    <message>
        <source>打印报告</source>
        <translation>Print report</translation>
    </message>
    <message>
        <source>审计追踪</source>
        <translation>Audit trail</translation>
    </message>
    <message>
        <source>仪器设置</source>
        <translation>Settings</translation>
    </message>
    <message>
        <source>快捷操作栏</source>
        <translation>Quick actions</translation>
    </message>
    <message>
        <source>菜单</source>
        <translation>Menu</translation>
    </message>
    <message>
        <source>更多</source>
        <translation>More</translation>
    </message>
    <message>
        <source>更多操作</source>
        <translation>More actions</translation>
    </message>
    <message>
        <source>展开或收起侧栏</source>
        <translation>Show or hide the sidebar</translation>
    </message>
    <message>
        <source>仪器已连接</source>
        <translation>Connected</translation>
    </message>
    <message>
        <source>仪器连接状态：已连接</source>
        <translation>Instrument status: connected</translation>
    </message>
    <message>
        <source>仪器未连接</source>
        <translation>Disconnected</translation>
    </message>
    <message>
        <source>仪器连接状态：未连接</source>
        <translation>Instrument status: disconnected</translation>
    </message>
    <message>
        <source>界面语言：</source>
        <translation>Language: </translation>
    </message>
    <message>
        <source>未登录</source>
        <translation>Not signed in</translation>
    </message>
    <message>
        <source>当前用户：</source>
        <translation>Current user: </translation>
    </message>
    <message>
        <source>修改密码</source>
        <translation>Change password</translation>
    </message>
    <message>
        <source>锁定屏幕</source>
        <translation>Lock screen</translation>
    </message>
    <message>
        <source>退出登录</source>
        <translation>Sign out</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
</context>
<context>
    <name>ReagentCard</name>
    <message>
        <source>点击填入并校验这个示例码</source>
        <translation>Click to fill in and verify this sample code</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
    <message>
        <source>已通过</source>
        <translation>Passed</translation>
    </message>
    <message>
        <source>已锁定</source>
        <translation>Locked</translation>
    </message>
    <message>
        <source>请重新扫码</source>
        <translation>Rescan</translation>
    </message>
    <message>
        <source>待扫码</source>
        <translation>Waiting</translation>
    </message>
    <message>
        <source>扫码或输入试剂码，按回车确认</source>
        <translation>Scan or type the reagent code, press Enter</translation>
    </message>
    <message>
        <source>{name}试剂码</source>
        <translation>{name} reagent code</translation>
    </message>
    <message>
        <source>校验</source>
        <translation>Verify</translation>
    </message>
    <message>
        <source>校验{name}</source>
        <translation>Verify {name}</translation>
    </message>
    <message>
        <source>示例码</source>
        <translation>Sample codes</translation>
    </message>
</context>
<context>
    <name>ReagentKind</name>
    <message>
        <source>鲎试剂</source>
        <translation>LAL reagent</translation>
    </message>
    <message>
        <source>内毒素</source>
        <translation>Endotoxin standard</translation>
    </message>
    <message>
        <source>检查用水</source>
        <translation>Water for BET</translation>
    </message>
</context>
<context>
    <name>ReagentResultView</name>
    <message>
        <source>（即将到期）</source>
        <translation> (expires soon)</translation>
    </message>
    <message>
        <source>标示灵敏度 {value:g} EU/mL</source>
        <translation>Labeled sensitivity {value:g} EU/mL</translation>
    </message>
</context>
<context>
    <name>ReagentScan</name>
    <message>
        <source>已备案</source>
        <translation>Registered</translation>
    </message>
    <message>
        <source>已过期</source>
        <translation>Expired</translation>
    </message>
    <message>
        <source>未备案</source>
        <translation>Not registered</translation>
    </message>
    <message>
        <source>有效期未知，无法确认在有效期内</source>
        <translation>Expiry unknown; cannot confirm it is in date</translation>
    </message>
    <message>
        <source>这是{found}，不是{expected}，请扫对应的试剂</source>
        <translation>This is {found}, not {expected}. Scan the matching reagent.</translation>
    </message>
    <message>
        <source>无法识别这个码，请重新扫码</source>
        <translation>Unrecognized code. Please scan again.</translation>
    </message>
    <message>
        <source>备案清单不可用，无法确认试剂合格</source>
        <translation>Registry unavailable; cannot confirm the reagent is valid</translation>
    </message>
    <message>
        <source>未配置试剂码解析或备案清单，无法确认试剂合格</source>
        <translation>No code parser or registry configured; cannot confirm the reagent is valid</translation>
    </message>
    <message>
        <source>效期至 {date}</source>
        <translation>expires {date}</translation>
    </message>
    <message>
        <source>已于 {date} 过期</source>
        <translation>Expired on {date}</translation>
    </message>
    <message>
        <source>剩余 {n} 天</source>
        <translation>{n} days left</translation>
    </message>
    <message>
        <source>今天到期</source>
        <translation>Expires today</translation>
    </message>
    <message>
        <source>已过期 {n} 天</source>
        <translation>Expired {n} days ago</translation>
    </message>
</context>
<context>
    <name>ReagentTile</name>
    <message>
        <source>未校验</source>
        <translation>Not verified</translation>
    </message>
</context>
<context>
    <name>ReagentsPage</name>
    <message>
        <source>种类</source>
        <translation>Kind</translation>
    </message>
    <message>
        <source>批号</source>
        <translation>Lot</translation>
    </message>
    <message>
        <source>效期</source>
        <translation>Expiry</translation>
    </message>
    <message>
        <source>剩余天数</source>
        <translation>Days left</translation>
    </message>
    <message>
        <source>状态</source>
        <translation>Status</translation>
    </message>
    <message>
        <source>最近校验</source>
        <translation>Last checked</translation>
    </message>
    <message>
        <source>时间</source>
        <translation>Time</translation>
    </message>
    <message>
        <source>操作者</source>
        <translation>Operator</translation>
    </message>
    <message>
        <source>结果</source>
        <translation>Result</translation>
    </message>
    <message>
        <source>{kind} · 查询结果不写入校验记录</source>
        <translation>{kind} · lookups are not recorded</translation>
    </message>
    <message>
        <source>已过期</source>
        <translation>Expired</translation>
    </message>
    <message>
        <source>未备案</source>
        <translation>Not registered</translation>
    </message>
    <message>
        <source>有效期未知</source>
        <translation>Expiry unknown</translation>
    </message>
    <message>
        <source>即将到期</source>
        <translation>Expiring soon</translation>
    </message>
    <message>
        <source>有效</source>
        <translation>Valid</translation>
    </message>
    <message>
        <source>还没有录入的试剂批次</source>
        <translation>No reagent lots recorded yet</translation>
    </message>
    <message>
        <source>在新试验向导里扫码校验后，批次会显示在这里</source>
        <translation>Lots appear here after they are verified in the wizard</translation>
    </message>
    <message>
        <source>通过</source>
        <translation>Passed</translation>
    </message>
    <message>
        <source>已过期，锁定</source>
        <translation>Expired, locked</translation>
    </message>
    <message>
        <source>未备案，锁定</source>
        <translation>Not registered, locked</translation>
    </message>
    <message>
        <source>有效期未知，锁定</source>
        <translation>Expiry unknown, locked</translation>
    </message>
    <message>
        <source>缺少，锁定</source>
        <translation>Missing, locked</translation>
    </message>
    <message>
        <source>还没有校验记录</source>
        <translation>No checks yet</translation>
    </message>
    <message>
        <source>接入预留</source>
        <translation>Reserved</translation>
    </message>
    <message>
        <source>政府溯源平台已启用，但本软件尚未实现上报，请等待接口规范。</source>
        <translation>The government platform is enabled, but reporting is not implemented yet. Waiting for the interface specification.</translation>
    </message>
    <message>
        <source>政府溯源平台未启用。接口按 interfaces/traceability.py 预留，平台规范明确后接入。</source>
        <translation>The government platform is not enabled. The interface is reserved in interfaces/traceability.py and will be connected once the specification is known.</translation>
    </message>
    <message>
        <source>尚未配置政府溯源客户端。接口按 interfaces/traceability.py 预留，平台规范明确后接入。</source>
        <translation>No government traceability client is configured. The interface is reserved in interfaces/traceability.py and will be connected once the specification is known.</translation>
    </message>
    <message>
        <source>试剂溯源</source>
        <translation>Reagent traceability</translation>
    </message>
    <message>
        <source>扫码查询</source>
        <translation>Scan lookup</translation>
    </message>
    <message>
        <source>扫码或输入试剂码，按回车查询</source>
        <translation>Scan or type a reagent code, press Enter</translation>
    </message>
    <message>
        <source>试剂码</source>
        <translation>Reagent code</translation>
    </message>
    <message>
        <source>查询</source>
        <translation>Look up</translation>
    </message>
    <message>
        <source>查询只检查备案和效期，不会录入批次。批次在新试验向导里扫码校验后录入。</source>
        <translation>A lookup only checks registration and expiry and does not record the lot. Lots are recorded when verified in the new experiment wizard.</translation>
    </message>
    <message>
        <source>已录入批次</source>
        <translation>Recorded lots</translation>
    </message>
    <message>
        <source>校验记录</source>
        <translation>Check log</translation>
    </message>
    <message>
        <source>政府溯源码</source>
        <translation>Government traceability code</translation>
    </message>
    <message>
        <source>政府溯源码（接入预留）</source>
        <translation>Government traceability code (reserved)</translation>
    </message>
    <message>
        <source>上报</source>
        <translation>Submit</translation>
    </message>
</context>
<context>
    <name>ReplayDialog</name>
    <message>
        <source>这一排没有录像</source>
        <translation>No recording for this row</translation>
    </message>
    <message>
        <source>按排回放</source>
        <translation>Row replay</translation>
    </message>
    <message>
        <source>关闭</source>
        <translation>Close</translation>
    </message>
</context>
<context>
    <name>ReplayPlayer</name>
    <message>
        <source>暂停</source>
        <translation>Pause</translation>
    </message>
    <message>
        <source>播放</source>
        <translation>Play</translation>
    </message>
    <message>
        <source>播放或暂停</source>
        <translation>Play or pause</translation>
    </message>
    <message>
        <source>回放进度</source>
        <translation>Replay position</translation>
    </message>
</context>
<context>
    <name>Report</name>
    <message>
        <source>自动凝胶检测报告</source>
        <translation>Automated Gel-Clot Test Report</translation>
    </message>
    <message>
        <source>单批次汇总报告</source>
        <translation>Lot Summary Report</translation>
    </message>
    <message>
        <source>文件名（试验编号）</source>
        <translation>File name (Exp. ID)</translation>
    </message>
    <message>
        <source>仪器编号</source>
        <translation>Instrument No.</translation>
    </message>
    <message>
        <source>检测日期</source>
        <translation>Test date</translation>
    </message>
    <message>
        <source>温度范围</source>
        <translation>Temperature range</translation>
    </message>
    <message>
        <source>操作人</source>
        <translation>Operator</translation>
    </message>
    <message>
        <source>签署人 / 日期</source>
        <translation>Signed by / date</translation>
    </message>
    <message>
        <source>未签署</source>
        <translation>Not signed</translation>
    </message>
    <message>
        <source>试验类型</source>
        <translation>Test type</translation>
    </message>
    <message>
        <source>鲎试剂灵敏度</source>
        <translation>LAL sensitivity</translation>
    </message>
    <message>
        <source>鲎试剂批号</source>
        <translation>LAL lot</translation>
    </message>
    <message>
        <source>内毒素批号</source>
        <translation>Endotoxin standard lot</translation>
    </message>
    <message>
        <source>检测用水批号</source>
        <translation>BET water lot</translation>
    </message>
    <message>
        <source>样品批号</source>
        <translation>Sample lot</translation>
    </message>
    <message>
        <source>MVD</source>
        <translation>MVD</translation>
    </message>
    <message>
        <source>内毒素限值</source>
        <translation>Endotoxin limit</translation>
    </message>
    <message>
        <source>试管编号</source>
        <translation>Tube No.</translation>
    </message>
    <message>
        <source>样品名称</source>
        <translation>Sample name</translation>
    </message>
    <message>
        <source>序列号</source>
        <translation>Serial No.</translation>
    </message>
    <message>
        <source>稀释倍数</source>
        <translation>Dilution</translation>
    </message>
    <message>
        <source>外加内毒素（EU/mL）</source>
        <translation>Spiked endotoxin (EU/mL)</translation>
    </message>
    <message>
        <source>阴阳性</source>
        <translation>Result</translation>
    </message>
    <message>
        <source>阳性</source>
        <translation>Positive</translation>
    </message>
    <message>
        <source>阴性</source>
        <translation>Negative</translation>
    </message>
    <message>
        <source>待复核</source>
        <translation>Pending review</translation>
    </message>
    <message>
        <source>无结果</source>
        <translation>No result</translation>
    </message>
    <message>
        <source>（已复核）</source>
        <translation> (reviewed)</translation>
    </message>
    <message>
        <source>结论</source>
        <translation>Conclusion</translation>
    </message>
    <message>
        <source>无</source>
        <translation>None</translation>
    </message>
    <message>
        <source>这个试验完成时还没有结论功能，没有入库的结论。</source>
        <translation>This experiment was completed before conclusions were introduced, so no conclusion was recorded.</translation>
    </message>
    <message>
        <source>实测灵敏度</source>
        <translation>measured sensitivity</translation>
    </message>
    <message>
        <source>（不是最终结论，不能作为放行依据）</source>
        <translation> (not a final conclusion; it must not be used as a release basis)</translation>
    </message>
    <message>
        <source>λc 参考值</source>
        <translation>λc reference value</translation>
    </message>
    <message>
        <source>不是最终结论</source>
        <translation>not a final conclusion</translation>
    </message>
    <message>
        <source>标示灵敏度 λ</source>
        <translation>Labeled sensitivity λ</translation>
    </message>
    <message>
        <source>无效原因</source>
        <translation>Reason for invalid</translation>
    </message>
    <message>
        <source>待定原因</source>
        <translation>Reason for pending</translation>
    </message>
    <message>
        <source>未满足的前提</source>
        <translation>Unmet prerequisites</translation>
    </message>
    <message>
        <source>依据（+ 阳性，− 阴性）</source>
        <translation>Basis (+ positive, − negative)</translation>
    </message>
    <message>
        <source>规则版本</source>
        <translation>Rule version</translation>
    </message>
    <message>
        <source>规则来源</source>
        <translation>Rule source</translation>
    </message>
    <message>
        <source>标注“已复核”的结果经分析员人工复核，以复核结果为准。</source>
        <translation>Results marked “reviewed” were reviewed manually by the analyst; the review result prevails.</translation>
    </message>
    <message>
        <source>8 连图（每排翻转前、翻转后代表帧）</source>
        <translation>8-tube images (representative frames before and after inversion, per row)</translation>
    </message>
    <message>
        <source>第 {row} 排</source>
        <translation>Row {row}</translation>
    </message>
    <message>
        <source>翻转前</source>
        <translation>Before inversion</translation>
    </message>
    <message>
        <source>翻转后</source>
        <translation>After inversion</translation>
    </message>
    <message>
        <source>没有图像</source>
        <translation>No image</translation>
    </message>
    <message>
        <source>试验编号</source>
        <translation>Experiment ID</translation>
    </message>
    <message>
        <source>试管数</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>状态</source>
        <translation>Status</translation>
    </message>
    <message>
        <source>试验</source>
        <translation>Experiment</translation>
    </message>
    <message>
        <source>共 {n} 个试验</source>
        <translation>{n} experiment(s)</translation>
    </message>
    <message>
        <source>本报告已使用数字签名，任何修改都会导致验签失败。</source>
        <translation>This report carries a digital signature; any modification makes the signature check fail.</translation>
    </message>
    <message>
        <source>本报告尚未签署，仅供预览和核对，不具备签署报告的效力。</source>
        <translation>This report has not been signed. It is for preview and checking only and has no effect as a signed report.</translation>
    </message>
    <message>
        <source>© {holder}，未经许可不得复制。</source>
        <translation>© {holder}. Reproduction without permission is prohibited.</translation>
    </message>
    <message>
        <source>内容摘要 SHA-256</source>
        <translation>Content digest SHA-256</translation>
    </message>
    <message>
        <source>第 {page} 页 / 共 {total} 页</source>
        <translation>Page {page} / {total}</translation>
    </message>
    <message>
        <source>自动凝胶检测报告 {id}</source>
        <translation>Automated Gel-Clot Test Report {id}</translation>
    </message>
    <message>
        <source>凝胶法细菌内毒素检测</source>
        <translation>Gel-clot bacterial endotoxin test</translation>
    </message>
    <message>
        <source>签署报告 {id}</source>
        <translation>Sign report {id}</translation>
    </message>
    <message>
        <source>[版权方名称]</source>
        <translation>[Copyright holder]</translation>
    </message>
</context>
<context>
    <name>ReportsPage</name>
    <message>
        <source>第 {page} / {total} 页</source>
        <translation>Page {page} / {total}</translation>
    </message>
    <message>
        <source>已签署</source>
        <translation>Signed</translation>
    </message>
    <message>
        <source>可以签署：试验结论为“{verdict}”</source>
        <translation>Ready to sign: the experiment conclusion is “{verdict}”</translation>
    </message>
    <message>
        <source>可以签署：批次里的试验都已签署</source>
        <translation>Ready to sign: every experiment of the lot is signed</translation>
    </message>
    <message>
        <source>可以签署：全部待复核已处理，试验已完成</source>
        <translation>Ready to sign: all tubes pending review have been handled and the experiment is complete</translation>
    </message>
    <message>
        <source>现在不能签署</source>
        <translation>Cannot be signed now</translation>
    </message>
    <message>
        <source>当前角色没有签署权限，签署需要分析员。</source>
        <translation>Your role has no signing permission; signing needs an analyst.</translation>
    </message>
    <message>
        <source>已经签署，可以重新验签或导出同一份文件。</source>
        <translation>Already signed. You can verify again or export the same file.</translation>
    </message>
    <message>
        <source>签署需要再次输入密码；签署后结果和复核被锁定。</source>
        <translation>Signing asks for your password again; results and review are locked afterwards.</translation>
    </message>
    <message>
        <source>单批次报告汇总已签署的试验，不改变试验状态。</source>
        <translation>A lot report summarizes signed experiments and does not change their status.</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Your role does not have this permission</translation>
    </message>
    <message>
        <source>已签署的报告语言固定为签署时的语言。</source>
        <translation>A signed report keeps the language it was signed in.</translation>
    </message>
    <message>
        <source>未签署时可以选择报告语言，签署时按所选语言生成。</source>
        <translation>Before signing you can choose the report language; the signed file uses it.</translation>
    </message>
    <message>
        <source>鲎试剂批号</source>
        <translation>LAL reagent lot</translation>
    </message>
    <message>
        <source>样品批号</source>
        <translation>Sample lot</translation>
    </message>
    <message>
        <source>还没有已完成的试验，没有可汇总的批次。</source>
        <translation>No completed experiments yet, so there is no lot to summarize.</translation>
    </message>
    <message>
        <source>已签署，报告已保存：{name}</source>
        <translation>Signed. Report saved: {name}</translation>
    </message>
    <message>
        <source>选择要验证的 PDF</source>
        <translation>Choose a PDF to verify</translation>
    </message>
    <message>
        <source>PDF 文件 (*.pdf)</source>
        <translation>PDF files (*.pdf)</translation>
    </message>
    <message>
        <source>已发送到打印机（{pages} 页）</source>
        <translation>Sent to the printer ({pages} page(s))</translation>
    </message>
    <message>
        <source>导出已签名 PDF</source>
        <translation>Export signed PDF</translation>
    </message>
    <message>
        <source>已导出：{name}</source>
        <translation>Exported: {name}</translation>
    </message>
    <message>
        <source>导出 Excel 数据</source>
        <translation>Export Excel data</translation>
    </message>
    <message>
        <source>Excel 文件 (*.xlsx)</source>
        <translation>Excel files (*.xlsx)</translation>
    </message>
    <message>
        <source>报告中心</source>
        <translation>Report Center</translation>
    </message>
    <message>
        <source>试验</source>
        <translation>Experiment</translation>
    </message>
    <message>
        <source>刷新</source>
        <translation>Refresh</translation>
    </message>
    <message>
        <source>还没有可出报告的试验</source>
        <translation>No experiments ready for a report</translation>
    </message>
    <message>
        <source>试验完成并复核后，在这里预览、打印和签署报告。可以先到历史记录里查看已有的试验。</source>
        <translation>After an experiment is completed and reviewed, preview, print and sign its report here. You can look at existing experiments in History first.</translation>
    </message>
    <message>
        <source>打开历史记录</source>
        <translation>Open History</translation>
    </message>
    <message>
        <source>签名状态</source>
        <translation>Signature status</translation>
    </message>
    <message>
        <source>签署人</source>
        <translation>Signed by</translation>
    </message>
    <message>
        <source>签署时间</source>
        <translation>Signed at</translation>
    </message>
    <message>
        <source>证书</source>
        <translation>Certificate</translation>
    </message>
    <message>
        <source>文件</source>
        <translation>File</translation>
    </message>
    <message>
        <source>语言</source>
        <translation>Language</translation>
    </message>
    <message>
        <source>重新验签</source>
        <translation>Verify again</translation>
    </message>
    <message>
        <source>验证外部 PDF…</source>
        <translation>Verify an external PDF…</translation>
    </message>
    <message>
        <source>签署和导出</source>
        <translation>Sign and export</translation>
    </message>
    <message>
        <source>报告语言</source>
        <translation>Report language</translation>
    </message>
    <message>
        <source>签署并生成 PDF</source>
        <translation>Sign and generate PDF</translation>
    </message>
    <message>
        <source>打印</source>
        <translation>Print</translation>
    </message>
    <message>
        <source>导出 Excel 数据…</source>
        <translation>Export Excel data…</translation>
    </message>
    <message>
        <source>批量导出…</source>
        <translation>Export in bulk…</translation>
    </message>
    <message>
        <source>单批次报告</source>
        <translation>Lot report</translation>
    </message>
    <message>
        <source>把同一批号的多次试验汇总到一份报告。</source>
        <translation>Summarize all experiments of one lot in a single report.</translation>
    </message>
    <message>
        <source>预览单批次报告</source>
        <translation>Preview lot report</translation>
    </message>
    <message>
        <source>返回试验报告</source>
        <translation>Back to experiment report</translation>
    </message>
</context>
<context>
    <name>Role</name>
    <message>
        <source>管理员</source>
        <translation>Administrator</translation>
    </message>
    <message>
        <source>实验员</source>
        <translation>Experimenter</translation>
    </message>
    <message>
        <source>分析员</source>
        <translation>Analyst</translation>
    </message>
</context>
<context>
    <name>RoleDialog</name>
    <message>
        <source>修改角色</source>
        <translation>Change role</translation>
    </message>
    <message>
        <source>修改 {name} 的角色</source>
        <translation>Change the role of {name}</translation>
    </message>
    <message>
        <source>角色</source>
        <translation>Role</translation>
    </message>
    <message>
        <source>改动立即生效：对方已登录的会话马上按新角色的权限工作。</source>
        <translation>Takes effect immediately: the user&apos;s signed-in session switches to the new role&apos;s permissions right away.</translation>
    </message>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>保存</source>
        <translation>Save</translation>
    </message>
</context>
<context>
    <name>Routes</name>
    <message>
        <source>登录</source>
        <translation>Sign in</translation>
    </message>
    <message>
        <source>工作台</source>
        <translation>Dashboard</translation>
    </message>
    <message>
        <source>新试验</source>
        <translation>New experiment</translation>
    </message>
    <message>
        <source>试验监控</source>
        <translation>Experiment monitor</translation>
    </message>
    <message>
        <source>单管详情</source>
        <translation>Tube detail</translation>
    </message>
    <message>
        <source>历史记录</source>
        <translation>History</translation>
    </message>
    <message>
        <source>模板管理</source>
        <translation>Template management</translation>
    </message>
    <message>
        <source>试剂溯源</source>
        <translation>Reagent traceability</translation>
    </message>
    <message>
        <source>报告中心</source>
        <translation>Report Center</translation>
    </message>
    <message>
        <source>审计追踪</source>
        <translation>Audit trail</translation>
    </message>
    <message>
        <source>用户与权限</source>
        <translation>Users &amp; permissions</translation>
    </message>
    <message>
        <source>仪器设置</source>
        <translation>Instrument settings</translation>
    </message>
    <message>
        <source>关于</source>
        <translation>About</translation>
    </message>
</context>
<context>
    <name>ServiceError</name>
    <message>
        <source>数据库正忙，操作没有生效，请稍后再试</source>
        <translation>The database is busy. Nothing was changed; try again shortly.</translation>
    </message>
    <message>
        <source>模板不存在</source>
        <translation>Template not found</translation>
    </message>
    <message>
        <source>内置模板 {name} 只读，请复制后再编辑</source>
        <translation>The built-in template {name} is read-only. Copy it to edit.</translation>
    </message>
    <message>
        <source>模板名称不能为空</source>
        <translation>The template name cannot be empty</translation>
    </message>
    <message>
        <source>已有同名模板：{name}</source>
        <translation>A template named {name} already exists</translation>
    </message>
    <message>
        <source>模板名称不能超过 {max} 个字符</source>
        <translation>The template name cannot exceed {max} characters</translation>
    </message>
    <message>
        <source>模板的试管数应在 1 到 {max} 之间</source>
        <translation>A template needs between 1 and {max} tubes</translation>
    </message>
    <message>
        <source>第 {index} 支试管的样品名称不能为空</source>
        <translation>Tube {index}: the sample name cannot be empty</translation>
    </message>
    <message>
        <source>第 {index} 支试管的数值不合法（不能为负数）</source>
        <translation>Tube {index}: invalid value (cannot be negative)</translation>
    </message>
    <message>
        <source>请先选择检测模板</source>
        <translation>Select a template first</translation>
    </message>
    <message>
        <source>模板要求 {expected} 支试管，实际安排了 {actual} 支</source>
        <translation>The template needs {expected} tubes but {actual} are assigned</translation>
    </message>
    <message>
        <source>管位不合法</source>
        <translation>Invalid tube positions</translation>
    </message>
    <message>
        <source>第 {index} 行的样品名称为空</source>
        <translation>Row {index}: the sample name is empty</translation>
    </message>
    <message>
        <source>试验不存在：{id}</source>
        <translation>Experiment not found: {id}</translation>
    </message>
    <message>
        <source>试验 {id} 不是就绪状态，不能启动</source>
        <translation>Experiment {id} is not ready and cannot be started</translation>
    </message>
    <message>
        <source>已有试验正在运行，设备同一时间只能运行一个试验</source>
        <translation>An experiment is already running; the instrument runs one at a time</translation>
    </message>
    <message>
        <source>引擎拒绝启动试验</source>
        <translation>The engine refused to start the experiment</translation>
    </message>
    <message>
        <source>试验 {id} 当前状态不能停止</source>
        <translation>Experiment {id} cannot be stopped in its current state</translation>
    </message>
    <message>
        <source>{tube} 号管没有结果</source>
        <translation>Tube {tube} has no result</translation>
    </message>
    <message>
        <source>复核意见必填，且不超过 {max} 个字符</source>
        <translation>A review note is required (at most {max} characters)</translation>
    </message>
    <message>
        <source>全部排读数分析完成、试验进入待复核后才能复核</source>
        <translation>Review is available once all rows are analyzed and the experiment is pending review</translation>
    </message>
    <message>
        <source>复核结论只能是阳性或阴性</source>
        <translation>The review conclusion must be positive or negative</translation>
    </message>
    <message>
        <source>试验状态已被其他操作改变，请刷新后重试</source>
        <translation>The experiment changed meanwhile; refresh and try again</translation>
    </message>
    <message>
        <source>这个试验现在不能恢复，软件重启后只能重试分析或停止</source>
        <translation>This experiment cannot be resumed now; after a restart it can only be re-analyzed or stopped</translation>
    </message>
    <message>
        <source>这个试验现在不能重试分析</source>
        <translation>Analysis cannot be retried for this experiment now</translation>
    </message>
    <message>
        <source>这个模板需要鲎试剂的标示灵敏度 λ，当前试剂没有提供</source>
        <translation>This template needs the labeled sensitivity λ of the LAL reagent, and the current reagent has none</translation>
    </message>
    <message>
        <source>第 {row} 排没有可播放的录像</source>
        <translation>Row {row} has no playable recording</translation>
    </message>
    <message>
        <source>第 {row} 排的录像与入库的哈希不符，可能已被改动</source>
        <translation>The recording of row {row} does not match its stored hash and may have been altered</translation>
    </message>
    <message>
        <source>试剂未通过校验，流程已锁定</source>
        <translation>Reagent check failed; the workflow is locked</translation>
    </message>
    <message>
        <source>试验已签署，结果和复核已锁定，不能再修改</source>
        <translation>The experiment is signed; results and review are locked and cannot be changed.</translation>
    </message>
    <message>
        <source>现在不能签署这个试验</source>
        <translation>This experiment cannot be signed right now.</translation>
    </message>
    <message>
        <source>签署失败，没有生成报告，试验状态没有改变：{reason}</source>
        <translation>Signing failed. No report was produced and the experiment status is unchanged: {reason}</translation>
    </message>
    <message>
        <source>报告不存在</source>
        <translation>The report does not exist.</translation>
    </message>
    <message>
        <source>报告文件不存在：{path}</source>
        <translation>Report file not found: {path}</translation>
    </message>
    <message>
        <source>报告文件与入库的 SHA-256 不符，可能已被改动：{name}</source>
        <translation>The report file does not match the stored SHA-256 and may have been modified: {name}</translation>
    </message>
    <message>
        <source>这个批号下没有已完成的试验</source>
        <translation>No completed experiments use this lot.</translation>
    </message>
    <message>
        <source>单批次报告只能在其中全部试验都已签署后签署</source>
        <translation>A lot report can only be signed after every experiment in it has been signed.</translation>
    </message>
    <message>
        <source>没有可导出的报告</source>
        <translation>There are no reports to export.</translation>
    </message>
    <message>
        <source>导出失败：{reason}</source>
        <translation>Export failed: {reason}</translation>
    </message>
    <message>
        <source>已开启导出加密，请设置导出密码</source>
        <translation>Export encryption is on. Please set an export password</translation>
    </message>
    <message>
        <source>导出密码至少 {min} 个字符</source>
        <translation>The export password must have at least {min} characters</translation>
    </message>
    <message>
        <source>用户不存在</source>
        <translation>User not found</translation>
    </message>
    <message>
        <source>登录名已存在：{name}</source>
        <translation>Login name already exists: {name}</translation>
    </message>
    <message>
        <source>不能停用或降级最后一个管理员，否则没有人能管理用户</source>
        <translation>The last administrator cannot be disabled or demoted; nobody could manage users</translation>
    </message>
    <message>
        <source>不能停用自己的账号</source>
        <translation>You cannot disable your own account</translation>
    </message>
    <message>
        <source>不能在这里重置自己的密码，请用用户菜单里的“修改密码”</source>
        <translation>You cannot reset your own password here; use Change password in the user menu</translation>
    </message>
    <message>
        <source>显示名称不能超过 {max} 个字符</source>
        <translation>The display name cannot exceed {max} characters</translation>
    </message>
    <message>
        <source>设置有误，没有保存</source>
        <translation>Invalid settings; nothing was saved</translation>
    </message>
    <message>
        <source>试验正在运行，现在不能修改影响运行的设置（孵育、读数、温度、视频输入）</source>
        <translation>An experiment is running; settings that affect it (incubation, readout, temperature, video input) cannot be changed now</translation>
    </message>
    <message>
        <source>设置没有保存：{reason}</source>
        <translation>Settings were not saved: {reason}</translation>
    </message>
    <message>
        <source>无法读取视频输入：{reason}</source>
        <translation>Cannot read the video input: {reason}</translation>
    </message>
    <message>
        <source>“{field}”必须是大于 0 的数字</source>
        <translation>&quot;{field}&quot; must be a number greater than 0</translation>
    </message>
    <message>
        <source>起始时间必须早于结束时间</source>
        <translation>The start time must be earlier than the end time</translation>
    </message>
</context>
<context>
    <name>Settings</name>
    <message>
        <source>仪器编号</source>
        <translation>Instrument No.</translation>
    </message>
    <message>
        <source>孵育时间（分钟）</source>
        <translation>Incubation time (min)</translation>
    </message>
    <message>
        <source>翻转前后各取帧数</source>
        <translation>Frames before and after the flip</translation>
    </message>
    <message>
        <source>翻转后稳定时间（秒）</source>
        <translation>Settle time after the flip (s)</translation>
    </message>
    <message>
        <source>目标温度（℃）</source>
        <translation>Target temperature (°C)</translation>
    </message>
    <message>
        <source>允许偏差 ±（℃）</source>
        <translation>Allowed deviation ± (°C)</translation>
    </message>
    <message>
        <source>越限告警持续时间（秒）</source>
        <translation>Out-of-range alarm duration (s)</translation>
    </message>
    <message>
        <source>视频输入类型</source>
        <translation>Video input type</translation>
    </message>
    <message>
        <source>视频路径</source>
        <translation>Video path</translation>
    </message>
    <message>
        <source>数据保留天数</source>
        <translation>Data retention (days)</translation>
    </message>
    <message>
        <source>版权方名称</source>
        <translation>Copyright holder</translation>
    </message>
    <message>
        <source>导出加密</source>
        <translation>Export encryption</translation>
    </message>
</context>
<context>
    <name>SettingsPage</name>
    <message>
        <source>第一次试验后显示</source>
        <translation>Shown after the first experiment</translation>
    </message>
    <message>
        <source>配置文件里的视频源：{source}</source>
        <translation>Video source in the configuration file: {source}</translation>
    </message>
    <message>
        <source>只读</source>
        <translation>Read-only</translation>
    </message>
    <message>
        <source>当前角色只能查看仪器设置，修改需要管理员权限。</source>
        <translation>Your role can only view the instrument settings; changing them needs the administrator role.</translation>
    </message>
    <message>
        <source>试验 {id} 正在运行</source>
        <translation>Experiment {id} is running</translation>
    </message>
    <message>
        <source>孵育、读数、温度和视频输入暂时不能修改；仪器编号、数据保留天数和版权方名称仍可修改。</source>
        <translation>Incubation, readout, temperature and video input cannot be changed for now; the instrument No., data retention and copyright holder can still be changed.</translation>
    </message>
    <message>
        <source>有效温度范围 {low:g} – {high:g} ℃</source>
        <translation>Valid temperature range {low:g} – {high:g} °C</translation>
    </message>
    <message>
        <source>视频文件的完整路径</source>
        <translation>Full path of the video file</translation>
    </message>
    <message>
        <source>图片文件夹的完整路径</source>
        <translation>Full path of the image folder</translation>
    </message>
    <message>
        <source>设置已保存，对之后启动的试验生效。</source>
        <translation>Settings saved; they apply to experiments started from now on.</translation>
    </message>
    <message>
        <source>没有改动。</source>
        <translation>No changes.</translation>
    </message>
    <message>
        <source>有 {count} 项设置不合法，没有保存。</source>
        <translation>{count} settings are invalid; nothing was saved.</translation>
    </message>
    <message>
        <source>选择视频文件</source>
        <translation>Choose a video file</translation>
    </message>
    <message>
        <source>视频文件 (*.mp4 *.avi *.mov *.mkv *.m4v);;所有文件 (*)</source>
        <translation>Video files (*.mp4 *.avi *.mov *.mkv *.m4v);;All files (*)</translation>
    </message>
    <message>
        <source>选择图片文件夹</source>
        <translation>Choose an image folder</translation>
    </message>
    <message>
        <source>正在读取……</source>
        <translation>Reading…</translation>
    </message>
    <message>
        <source>{fps:g} 帧/秒</source>
        <translation>{fps:g} fps</translation>
    </message>
    <message>
        <source>帧率未知</source>
        <translation>Frame rate unknown</translation>
    </message>
    <message>
        <source>{n} 帧</source>
        <translation>{n} frames</translation>
    </message>
    <message>
        <source>读不到画面</source>
        <translation>No picture could be read</translation>
    </message>
    <message>
        <source>无法预览</source>
        <translation>Cannot preview</translation>
    </message>
    <message>
        <source>操作失败：{reason}</source>
        <translation>Operation failed: {reason}</translation>
    </message>
    <message>
        <source>仪器设置</source>
        <translation>Instrument Settings</translation>
    </message>
    <message>
        <source>改动保存在数据目录的用户配置里，对之后启动的试验生效</source>
        <translation>Changes are saved in the user configuration in the data folder and apply to experiments started afterwards</translation>
    </message>
    <message>
        <source>仪器与数据</source>
        <translation>Instrument and data</translation>
    </message>
    <message>
        <source>孵育与读数</source>
        <translation>Incubation and readout</translation>
    </message>
    <message>
        <source>温度</source>
        <translation>Temperature</translation>
    </message>
    <message>
        <source>视频输入</source>
        <translation>Video input</translation>
    </message>
    <message>
        <source>算法后端</source>
        <translation>Analysis backend</translation>
    </message>
    <message>
        <source>留空表示不自动删除（默认）。到期清理尚未启用，目前不会删除任何文件。</source>
        <translation>Leave empty for no automatic deletion (default). Expiry cleanup is not enabled yet, so no files are deleted for now.</translation>
    </message>
    <message>
        <source>敏感导出加密（Excel、批量导出报告打成带密码的 ZIP）</source>
        <translation>Encrypt sensitive exports (Excel files and bulk report exports become password-protected ZIP files)</translation>
    </message>
    <message>
        <source>开启后每次导出都要设置密码，文件为 AES-256 加密的 ZIP。请用 7-Zip、Keka 等工具解压，系统自带的解压不支持这种加密。</source>
        <translation>When on, every export asks for a password and produces an AES-256 encrypted ZIP. Extract with 7-Zip, Keka or similar; the built-in system tools do not support it.</translation>
    </message>
    <message>
        <source>写入之后新建的每个试验和报告。</source>
        <translation>Written into every experiment and report created afterwards.</translation>
    </message>
    <message>
        <source>沿用配置文件</source>
        <translation>Use the configuration file</translation>
    </message>
    <message>
        <source>视频文件</source>
        <translation>Video file</translation>
    </message>
    <message>
        <source>图片文件夹</source>
        <translation>Image folder</translation>
    </message>
    <message>
        <source>浏览…</source>
        <translation>Browse…</translation>
    </message>
    <message>
        <source>预览</source>
        <translation>Preview</translation>
    </message>
    <message>
        <source>工业相机：型号确定后接入。</source>
        <translation>Industrial camera: to be connected once the model is decided.</translation>
    </message>
    <message>
        <source>后端</source>
        <translation>Backend</translation>
    </message>
    <message>
        <source>模型版本</source>
        <translation>Model version</translation>
    </message>
    <message>
        <source>配置版本</source>
        <translation>Config version</translation>
    </message>
    <message>
        <source>配置文件</source>
        <translation>Config file</translation>
    </message>
    <message>
        <source>还原</source>
        <translation>Revert</translation>
    </message>
    <message>
        <source>保存设置</source>
        <translation>Save settings</translation>
    </message>
</context>
<context>
    <name>SettingsProblem</name>
    <message>
        <source>{name}不能为空</source>
        <translation>{name} cannot be empty</translation>
    </message>
    <message>
        <source>{name}必须是数字，范围 {min:g}–{max:g}</source>
        <translation>{name} must be a number between {min:g} and {max:g}</translation>
    </message>
    <message>
        <source>{name}必须是整数，范围 {min:g}–{max:g}</source>
        <translation>{name} must be an integer between {min:g} and {max:g}</translation>
    </message>
    <message>
        <source>{name}应在 {min:g}–{max:g} 之间</source>
        <translation>{name} must be between {min:g} and {max:g}</translation>
    </message>
    <message>
        <source>{name}不能超过 {max:g} 个字符</source>
        <translation>{name} cannot exceed {max:g} characters</translation>
    </message>
    <message>
        <source>{name}不能含控制字符</source>
        <translation>{name} cannot contain control characters</translation>
    </message>
    <message>
        <source>路径不存在：{path}</source>
        <translation>Path does not exist: {path}</translation>
    </message>
    <message>
        <source>这不是一个文件：{path}</source>
        <translation>Not a file: {path}</translation>
    </message>
    <message>
        <source>这不是一个文件夹：{path}</source>
        <translation>Not a folder: {path}</translation>
    </message>
    <message>
        <source>文件夹里没有 JPG 或 PNG 图片：{path}</source>
        <translation>No JPG or PNG images in the folder: {path}</translation>
    </message>
</context>
<context>
    <name>SetupPage</name>
    <message>
        <source>两次输入的密码不一致</source>
        <translation>The passwords do not match.</translation>
    </message>
    <message>
        <source>创建管理员</source>
        <translation>Create administrator</translation>
    </message>
    <message>
        <source>这是第一次启动，系统里还没有账号。请创建管理员账号；首次登录后需要修改密码。</source>
        <translation>This is the first launch and there are no accounts yet. Create an administrator account; you will be asked to change the password at first sign-in.</translation>
    </message>
    <message>
        <source>登录名</source>
        <translation>Username</translation>
    </message>
    <message>
        <source>显示名称</source>
        <translation>Display name</translation>
    </message>
    <message>
        <source>密码（至少 8 个字符）</source>
        <translation>Password (at least 8 characters)</translation>
    </message>
    <message>
        <source>确认密码</source>
        <translation>Confirm password</translation>
    </message>
</context>
<context>
    <name>SideNav</name>
    <message>
        <source>工作台</source>
        <translation>Dashboard</translation>
    </message>
    <message>
        <source>新试验</source>
        <translation>New experiment</translation>
    </message>
    <message>
        <source>试验监控</source>
        <translation>Experiment monitor</translation>
    </message>
    <message>
        <source>历史记录</source>
        <translation>History</translation>
    </message>
    <message>
        <source>模板管理</source>
        <translation>Templates</translation>
    </message>
    <message>
        <source>试剂溯源</source>
        <translation>Reagent traceability</translation>
    </message>
    <message>
        <source>报告中心</source>
        <translation>Report Center</translation>
    </message>
    <message>
        <source>审计追踪</source>
        <translation>Audit trail</translation>
    </message>
    <message>
        <source>用户与权限</source>
        <translation>Users &amp; permissions</translation>
    </message>
    <message>
        <source>仪器设置</source>
        <translation>Instrument settings</translation>
    </message>
    <message>
        <source>关于</source>
        <translation>About</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
    <message>
        <source>主导航</source>
        <translation>Main navigation</translation>
    </message>
    <message>
        <source>凝胶智检系统</source>
        <translation>GelInspect</translation>
    </message>
</context>
<context>
    <name>SignDialog</name>
    <message>
        <source>签署报告</source>
        <translation>Sign report</translation>
    </message>
    <message>
        <source>{signer} 将签署 {subject}。请再次输入你的登录密码确认身份。</source>
        <translation>{signer} is about to sign {subject}. Enter your login password again to confirm your identity.</translation>
    </message>
    <message>
        <source>登录密码</source>
        <translation>Login password</translation>
    </message>
    <message>
        <source>签署后试验的结果和复核将被锁定，生成带数字签名的 PDF，不能撤销。</source>
        <translation>After signing, the experiment&apos;s results and review are locked and a digitally signed PDF is produced. This cannot be undone.</translation>
    </message>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>签署</source>
        <translation>Sign</translation>
    </message>
</context>
<context>
    <name>Signing</name>
    <message>
        <source>试验已签署，结果和复核已锁定，不能再修改。</source>
        <translation>The experiment is signed; results and review are locked and cannot be changed.</translation>
    </message>
    <message>
        <source>试验还没有完成（当前状态：{state}），完成并复核后才能签署。</source>
        <translation>The experiment is not completed (status: {state}); it can be signed after completion and review.</translation>
    </message>
    <message>
        <source>{tubes} 号管等待人工复核，复核全部完成后才能签署。</source>
        <translation>Tube(s) {tubes} await manual review; signing is possible once all reviews are done.</translation>
    </message>
    <message>
        <source>{tubes} 号管还没有结果。</source>
        <translation>Tube(s) {tubes} have no result yet.</translation>
    </message>
    <message>
        <source>这个试验没有入库的结论，不能签署。</source>
        <translation>This experiment has no recorded conclusion and cannot be signed.</translation>
    </message>
    <message>
        <source>试验结论为“待定”，不是最终结论，不能签署：未判定的结果不能被当成放行依据。</source>
        <translation>The conclusion is “Pending”, not final, so it cannot be signed: an undecided result must not become a release basis.</translation>
    </message>
    <message>
        <source>试验结论为“无效”，不能签署：不得据此放行。请处理下面的无效原因后重新做试验。</source>
        <translation>The conclusion is “Invalid”, so it cannot be signed and must not be used for release. Address the reasons below and repeat the test.</translation>
    </message>
    <message>
        <source>批次里还有试验没有签署，全部签署后才能签署单批次报告。</source>
        <translation>Some experiments of the lot are not signed yet; sign them all before signing the lot report.</translation>
    </message>
</context>
<context>
    <name>Sparkline</name>
    <message>
        <source>趋势曲线，最近值 {last}，范围 {low} 到 {high}</source>
        <translation>Trend chart, latest {last}, range {low} to {high}</translation>
    </message>
    <message>
        <source>趋势曲线，暂无数据</source>
        <translation>Trend chart, no data</translation>
    </message>
    <message>
        <source>60 分钟前</source>
        <translation>60 min ago</translation>
    </message>
    <message>
        <source>现在</source>
        <translation>Now</translation>
    </message>
</context>
<context>
    <name>Startup</name>
    <message>
        <source>（数据目录下的数据库文件）</source>
        <translation>(the database file in the data folder)</translation>
    </message>
    <message>
        <source>配置有误，无法启动</source>
        <translation>Invalid configuration</translation>
    </message>
    <message>
        <source>{error}

请修改配置文件后重新启动；不带 --config 参数启动会使用默认的模拟配置。</source>
        <translation>{error}

Fix the configuration file and start again. Starting without --config uses the default simulation configuration.</translation>
    </message>
    <message>
        <source>数据库版本不兼容</source>
        <translation>Incompatible database version</translation>
    </message>
    <message>
        <source>数据库 {path} 由更新版本的程序创建，当前版本无法打开。请升级软件，或改用与本版本匹配的数据目录。</source>
        <translation>The database {path} was created by a newer version and cannot be opened. Upgrade the software or use a data folder that matches this version.</translation>
    </message>
    <message>
        <source>数据库被占用</source>
        <translation>Database is in use</translation>
    </message>
    <message>
        <source>数据库 {path} 正被另一个程序占用，可能是另一个正在运行的 GelInspect。请关闭它们后重新启动。</source>
        <translation>The database {path} is in use by another program, possibly another running GelInspect. Close it and start again.</translation>
    </message>
    <message>
        <source>无法打开数据库或数据目录</source>
        <translation>Cannot open the database or data folder</translation>
    </message>
    <message>
        <source>无法使用数据库 {path}。请确认数据目录存在、有读写权限且磁盘未满，然后重新启动。</source>
        <translation>Cannot use the database {path}. Make sure the data folder exists, is writable and the disk is not full, then start again.</translation>
    </message>
    <message>
        <source>启动失败</source>
        <translation>Startup failed</translation>
    </message>
    <message>
        <source>程序启动时遇到意外错误。请把日志文件发给技术支持。</source>
        <translation>An unexpected error occurred during startup. Please send the log file to technical support.</translation>
    </message>
    <message>
        <source>程序遇到未处理的错误</source>
        <translation>Unhandled error</translation>
    </message>
    <message>
        <source>程序遇到了意外错误，已写入日志。当前操作可能没有完成；建议保存工作后重新启动。</source>
        <translation>An unexpected error occurred and was written to the log. The current action may not have completed; consider saving your work and restarting.</translation>
    </message>
    <message>
        <source>程序文件校验未通过</source>
        <translation>Program file check failed</translation>
    </message>
    <message>
        <source>程序目录里有文件与发布时的清单不一致，程序可能被改动或损坏。这件事已写入审计追踪。请联系管理员核实，必要时重新安装软件。</source>
        <translation>Some files in the program folder do not match the release manifest; the program may have been modified or damaged. This has been recorded in the audit trail. Ask your administrator to check, and reinstall if needed.</translation>
    </message>
</context>
<context>
    <name>StepIndicator</name>
    <message>
        <source>步骤 {no} / {total}：{name}</source>
        <translation>Step {no} of {total}: {name}</translation>
    </message>
</context>
<context>
    <name>TemplateEditorDialog</name>
    <message>
        <source>管号</source>
        <translation>Tube</translation>
    </message>
    <message>
        <source>样品名称</source>
        <translation>Sample name</translation>
    </message>
    <message>
        <source>稀释倍数</source>
        <translation>Dilution</translation>
    </message>
    <message>
        <source>外加内毒素</source>
        <translation>Spiked endotoxin</translation>
    </message>
    <message>
        <source>λ 倍数</source>
        <translation>λ multiple</translation>
    </message>
    <message>
        <source>用途</source>
        <translation>Purpose</translation>
    </message>
    <message>
        <source>共 {count} 支试管</source>
        <translation>{count} tubes</translation>
    </message>
    <message>
        <source>编辑模板</source>
        <translation>Edit template</translation>
    </message>
    <message>
        <source>新建模板</source>
        <translation>New template</translation>
    </message>
    <message>
        <source>模板名称</source>
        <translation>Template name</translation>
    </message>
    <message>
        <source>试验类型</source>
        <translation>Experiment type</translation>
    </message>
    <message>
        <source>添加试管</source>
        <translation>Add tube</translation>
    </message>
    <message>
        <source>删除最后一支</source>
        <translation>Remove last</translation>
    </message>
    <message>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <source>保存</source>
        <translation>Save</translation>
    </message>
</context>
<context>
    <name>TemplateName</name>
    <message>
        <source>灵敏度复核 (2λ:1)</source>
        <translation>Sensitivity confirmation (2λ:1)</translation>
    </message>
    <message>
        <source>灵敏度复核</source>
        <translation>Sensitivity confirmation</translation>
    </message>
    <message>
        <source>干扰试验</source>
        <translation>Interference test</translation>
    </message>
    <message>
        <source>限度试验（复试）</source>
        <translation>Limit test (retest)</translation>
    </message>
    <message>
        <source>半定量试验</source>
        <translation>Semi-quantitative test</translation>
    </message>
    <message>
        <source>限度试验</source>
        <translation>Limit test</translation>
    </message>
    <message>
        <source>阴性对照</source>
        <translation>Negative control</translation>
    </message>
    <message>
        <source>阳性对照</source>
        <translation>Positive control</translation>
    </message>
    <message>
        <source>供试品</source>
        <translation>Sample</translation>
    </message>
    <message>
        <source>供试品阳性对照</source>
        <translation>Positive product control</translation>
    </message>
    <message>
        <source>内毒素标准系列</source>
        <translation>Endotoxin standard series</translation>
    </message>
    <message>
        <source>供试品内毒素系列</source>
        <translation>Spiked sample series</translation>
    </message>
</context>
<context>
    <name>TemplateOption</name>
    <message>
        <source>内置</source>
        <translation>Built-in</translation>
    </message>
    <message>
        <source>自定义</source>
        <translation>Custom</translation>
    </message>
    <message>
        <source>{type} · {count} 管 · {source}</source>
        <translation>{type} · {count} tubes · {source}</translation>
    </message>
    <message>
        <source>模板 {name}，{count} 管</source>
        <translation>Template {name}, {count} tubes</translation>
    </message>
</context>
<context>
    <name>TemplatesPage</name>
    <message>
        <source>名称</source>
        <translation>Name</translation>
    </message>
    <message>
        <source>类型</source>
        <translation>Type</translation>
    </message>
    <message>
        <source>管数</source>
        <translation>Tubes</translation>
    </message>
    <message>
        <source>来源</source>
        <translation>Source</translation>
    </message>
    <message>
        <source>管号</source>
        <translation>Tube</translation>
    </message>
    <message>
        <source>样品名称</source>
        <translation>Sample name</translation>
    </message>
    <message>
        <source>用途</source>
        <translation>Purpose</translation>
    </message>
    <message>
        <source>稀释倍数</source>
        <translation>Dilution</translation>
    </message>
    <message>
        <source>外加内毒素</source>
        <translation>Spiked endotoxin</translation>
    </message>
    <message>
        <source>模板已创建</source>
        <translation>Template created</translation>
    </message>
    <message>
        <source>模板已复制，可以点“编辑”修改</source>
        <translation>Template copied. Click Edit to change it.</translation>
    </message>
    <message>
        <source>模板已保存</source>
        <translation>Template saved</translation>
    </message>
    <message>
        <source>内置</source>
        <translation>Built-in</translation>
    </message>
    <message>
        <source>自定义</source>
        <translation>Custom</translation>
    </message>
    <message>
        <source>还没有模板</source>
        <translation>No templates yet</translation>
    </message>
    <message>
        <source>管位详情</source>
        <translation>Tube details</translation>
    </message>
    <message>
        <source>管位详情 · {name}</source>
        <translation>Tube details · {name}</translation>
    </message>
    <message>
        <source>当前角色无此权限</source>
        <translation>Not permitted for your role</translation>
    </message>
    <message>
        <source>内置模板只读，请先复制再编辑</source>
        <translation>Built-in templates are read-only. Copy one to edit it.</translation>
    </message>
    <message>
        <source>内置模板只读，可以复制后修改。自定义模板保存后，新试验向导里立即可选。</source>
        <translation>Built-in templates are read-only; copy one to modify it. A saved custom template is available in the wizard right away.</translation>
    </message>
    <message>
        <source>当前角色只能查看模板，新建、复制和编辑需要管理员。</source>
        <translation>Your role can only view templates. Creating, copying and editing need an administrator.</translation>
    </message>
    <message>
        <source>模板管理</source>
        <translation>Templates</translation>
    </message>
    <message>
        <source>只读</source>
        <translation>Read-only</translation>
    </message>
    <message>
        <source>新建模板</source>
        <translation>New template</translation>
    </message>
    <message>
        <source>复制</source>
        <translation>Copy</translation>
    </message>
    <message>
        <source>编辑</source>
        <translation>Edit</translation>
    </message>
    <message>
        <source>检测模板</source>
        <translation>Templates</translation>
    </message>
    <message>
        <source>没有选中的模板</source>
        <translation>No template selected</translation>
    </message>
</context>
<context>
    <name>TubeDetailPage</name>
    <message>
        <source>{no} 号管不属于这个试验</source>
        <translation>Tube {no} is not part of this experiment</translation>
    </message>
    <message>
        <source>单管详情</source>
        <translation>Tube detail</translation>
    </message>
    <message>
        <source>正在读取图像…</source>
        <translation>Loading images…</translation>
    </message>
    <message>
        <source>这支试管没有抓拍图像</source>
        <translation>No captured image for this tube</translation>
    </message>
    <message>
        <source>没有这一帧</source>
        <translation>No such frame</translation>
    </message>
    <message>
        <source>读取图像失败</source>
        <translation>Failed to read images</translation>
    </message>
    <message>
        <source>{no} 号管</source>
        <translation>Tube {no}</translation>
    </message>
    <message>
        <source> · 外加内毒素 {v} EU/ml</source>
        <translation> · spiked endotoxin {v} EU/ml</translation>
    </message>
    <message>
        <source>试验 {id}</source>
        <translation>Experiment {id}</translation>
    </message>
    <message>
        <source>面积变化率 ΔA</source>
        <translation>Area change ΔA</translation>
    </message>
    <message>
        <source>质心偏移 Δs</source>
        <translation>Centroid shift Δs</translation>
    </message>
    <message>
        <source>面积波动 σ_A</source>
        <translation>Area variation σ_A</translation>
    </message>
    <message>
        <source>位置波动 σ_s</source>
        <translation>Position variation σ_s</translation>
    </message>
    <message>
        <source>暂无结果</source>
        <translation>No result</translation>
    </message>
    <message>
        <source>这支试管还没有分析结果</source>
        <translation>This tube has no analysis result yet</translation>
    </message>
    <message>
        <source>算法：{algo} → 人工：{human}</source>
        <translation>Algorithm: {algo} → Manual: {human}</translation>
    </message>
    <message>
        <source>已人工复核，以人工结论为准；算法原始结果保留</source>
        <translation>Manually reviewed; the manual conclusion applies and the algorithm result is kept</translation>
    </message>
    <message>
        <source>算法结果 · 需要人工复核后才能签署</source>
        <translation>Algorithm result · must be reviewed before signing</translation>
    </message>
    <message>
        <source>算法结果 · {v}</source>
        <translation>Algorithm result · {v}</translation>
    </message>
    <message>
        <source>未凝固 ≤ {low} · 部分凝固 · 完全凝固 ≥ {high}</source>
        <translation>Not gelled ≤ {low} · Partially gelled · Fully gelled ≥ {high}</translation>
    </message>
    <message>
        <source>还没有分析结果</source>
        <translation>No analysis result yet</translation>
    </message>
    <message>
        <source>算法没有提供评分分项，只显示上面的特征值和评分。</source>
        <translation>The analyzer gave no score breakdown; only the features and score above are shown.</translation>
    </message>
    <message>
        <source>测量值 → 归一化得分 f（0–1，越大越像凝胶）× 权重 ω = 贡献，各项相加为 Score。</source>
        <translation>Value → normalized score f (0–1, higher means more gel-like) × weight ω = contribution; the contributions add up to the score.</translation>
    </message>
    <message>
        <source>凝固状态 → 阴阳性：{state} → {result}</source>
        <translation>Gel state → result: {state} → {result}</translation>
    </message>
    <message>
        <source>{s} s（本排 {n} 支）</source>
        <translation>{s} s ({n} tubes in this row)</translation>
    </message>
    <message>
        <source>翻转前</source>
        <translation>Before flip</translation>
    </message>
    <message>
        <source>翻转后</source>
        <translation>After flip</translation>
    </message>
    <message>
        <source>这支试管还没有分析结果，不能复核</source>
        <translation>No analysis result yet, so it cannot be reviewed</translation>
    </message>
    <message>
        <source>当前角色没有复核权限，只有分析员可以复核</source>
        <translation>This role cannot review; only analysts can</translation>
    </message>
    <message>
        <source>试验已无待复核的试管</source>
        <translation>No tubes left to review</translation>
    </message>
    <message>
        <source>全部排读数分析完成、试验进入待复核后才能复核</source>
        <translation>Review is available once all rows are analyzed and the experiment is pending review</translation>
    </message>
    <message>
        <source>已由 {who} 于 {when} 复核为{v}。意见：{note}</source>
        <translation>Reviewed by {who} at {when} as {v}. Note: {note}</translation>
    </message>
    <message>
        <source>放弃未提交的复核？</source>
        <translation>Discard the unsubmitted review?</translation>
    </message>
    <message>
        <source>复核意见还没有提交，离开后会丢失。</source>
        <translation>The review note has not been submitted and will be lost.</translation>
    </message>
    <message>
        <source>放弃并离开</source>
        <translation>Discard and leave</translation>
    </message>
    <message>
        <source>留在本页</source>
        <translation>Stay</translation>
    </message>
    <message>
        <source>请先选择判为阳性或判为阴性</source>
        <translation>Choose positive or negative first</translation>
    </message>
    <message>
        <source>复核已提交</source>
        <translation>Review submitted</translation>
    </message>
    <message>
        <source>全部待复核的试管已处理，试验可以签署</source>
        <translation>All tubes reviewed; the experiment can be signed</translation>
    </message>
    <message>
        <source>收起 3 帧序列</source>
        <translation>Hide 3-frame sequence</translation>
    </message>
    <message>
        <source>查看 3 帧序列</source>
        <translation>View 3-frame sequence</translation>
    </message>
    <message>
        <source>返回孔板</source>
        <translation>Back to plate</translation>
    </message>
    <message>
        <source>翻转前后对比</source>
        <translation>Before and after flip</translation>
    </message>
    <message>
        <source>显示算法掩码</source>
        <translation>Show algorithm masks</translation>
    </message>
    <message>
        <source>回放翻转视频</source>
        <translation>Replay flip video</translation>
    </message>
    <message>
        <source>融合评分 Score</source>
        <translation>Fused score</translation>
    </message>
    <message>
        <source>判读过程</source>
        <translation>Reading breakdown</translation>
    </message>
    <message>
        <source>特征</source>
        <translation>Feature</translation>
    </message>
    <message>
        <source>测量值</source>
        <translation>Value</translation>
    </message>
    <message>
        <source>归一化 f</source>
        <translation>Normalized f</translation>
    </message>
    <message>
        <source>权重 ω</source>
        <translation>Weight ω</translation>
    </message>
    <message>
        <source>贡献</source>
        <translation>Contribution</translation>
    </message>
    <message>
        <source>判读依据</source>
        <translation>Reading criteria</translation>
    </message>
    <message>
        <source>人工复核</source>
        <translation>Manual review</translation>
    </message>
    <message>
        <source>判为阳性</source>
        <translation>Mark positive</translation>
    </message>
    <message>
        <source>判为阴性</source>
        <translation>Mark negative</translation>
    </message>
    <message>
        <source>复核意见（必填）</source>
        <translation>Review note (required)</translation>
    </message>
    <message>
        <source>说明判定依据，会写入审计追踪</source>
        <translation>Explain the basis; it goes into the audit trail</translation>
    </message>
    <message>
        <source>提交复核</source>
        <translation>Submit review</translation>
    </message>
    <message>
        <source>判读来源</source>
        <translation>Analysis source</translation>
    </message>
    <message>
        <source>模型版本</source>
        <translation>Model version</translation>
    </message>
    <message>
        <source>阈值配置</source>
        <translation>Threshold config</translation>
    </message>
    <message>
        <source>判读耗时</source>
        <translation>Analysis time</translation>
    </message>
    <message>
        <source>暂无图像</source>
        <translation>No image</translation>
    </message>
    <message>
        <source>从试验监控的孔板或结果表里点一支试管查看详情。</source>
        <translation>Click a tube on the monitor plate or results table to see its detail.</translation>
    </message>
</context>
<context>
    <name>TubeRole</name>
    <message>
        <source>内毒素标准系列</source>
        <translation>Endotoxin standard series</translation>
    </message>
    <message>
        <source>阴性对照</source>
        <translation>Negative control</translation>
    </message>
    <message>
        <source>供试品</source>
        <translation>Sample</translation>
    </message>
    <message>
        <source>阳性对照</source>
        <translation>Positive control</translation>
    </message>
    <message>
        <source>供试品阳性对照</source>
        <translation>Positive product control</translation>
    </message>
    <message>
        <source>供试品内毒素系列</source>
        <translation>Spiked sample series</translation>
    </message>
    <message>
        <source>未指定</source>
        <translation>Not assigned</translation>
    </message>
</context>
<context>
    <name>TubeStrip</name>
    <message>
        <source>当前一排 8 支试管的实时画面</source>
        <translation>Live view of the 8 tubes in the current row</translation>
    </message>
</context>
<context>
    <name>UsersPage</name>
    <message>
        <source>启用</source>
        <translation>Enable</translation>
    </message>
    <message>
        <source>已停用</source>
        <translation>Disabled</translation>
    </message>
    <message>
        <source>使用中</source>
        <translation>Active</translation>
    </message>
    <message>
        <source>须改密</source>
        <translation>Must change password</translation>
    </message>
    <message>
        <source>（我）</source>
        <translation> (you)</translation>
    </message>
    <message>
        <source>自己的密码请用用户菜单里的“修改密码”</source>
        <translation>Use Change password in the user menu for your own password</translation>
    </message>
    <message>
        <source>不能停用自己的账号</source>
        <translation>You cannot disable your own account</translation>
    </message>
    <message>
        <source>停用</source>
        <translation>Disable</translation>
    </message>
    <message>
        <source>重置密码？</source>
        <translation>Reset password?</translation>
    </message>
    <message>
        <source>将为 {name} 生成一个临时密码。对方已登录的会话会立即失效，下次登录必须先修改密码。</source>
        <translation>A temporary password will be generated for {name}. Their signed-in sessions end immediately and they must change the password at the next sign-in.</translation>
    </message>
    <message>
        <source>重置</source>
        <translation>Reset</translation>
    </message>
    <message>
        <source>停用账号？</source>
        <translation>Disable account?</translation>
    </message>
    <message>
        <source>{name} 将不能再登录，已登录的会话立即失效。可以随时重新启用。</source>
        <translation>{name} will no longer be able to sign in and signed-in sessions end immediately. You can enable the account again at any time.</translation>
    </message>
    <message>
        <source>已启用 {name}</source>
        <translation>{name} enabled</translation>
    </message>
    <message>
        <source>已停用 {name}</source>
        <translation>{name} disabled</translation>
    </message>
    <message>
        <source>登录名</source>
        <translation>Login name</translation>
    </message>
    <message>
        <source>显示名称</source>
        <translation>Display name</translation>
    </message>
    <message>
        <source>角色</source>
        <translation>Role</translation>
    </message>
    <message>
        <source>状态</source>
        <translation>Status</translation>
    </message>
    <message>
        <source>权限</source>
        <translation>Permission</translation>
    </message>
    <message>
        <source>用户与权限</source>
        <translation>Users and Permissions</translation>
    </message>
    <message>
        <source>改动立即生效，并记入审计追踪</source>
        <translation>Changes take effect immediately and are recorded in the audit trail</translation>
    </message>
    <message>
        <source>新建用户</source>
        <translation>New user</translation>
    </message>
    <message>
        <source>账号</source>
        <translation>Accounts</translation>
    </message>
    <message>
        <source>重置密码</source>
        <translation>Reset password</translation>
    </message>
    <message>
        <source>修改角色…</source>
        <translation>Change role…</translation>
    </message>
    <message>
        <source>不能停用自己，也不能停用或降级最后一个管理员。重置密码后，对方首次登录必须先改密。</source>
        <translation>You cannot disable yourself, nor disable or demote the last administrator. After a reset, the user must change the password at the next sign-in.</translation>
    </message>
    <message>
        <source>还没有账号</source>
        <translation>No accounts yet</translation>
    </message>
    <message>
        <source>角色与权限</source>
        <translation>Roles and permissions</translation>
    </message>
    <message>
        <source>只读。权限矩阵是软件的一部分，调整需要发布新版本；它也是界面入口和服务层检查的依据。</source>
        <translation>Read-only. The permission matrix is part of the software and changing it needs a new release; it drives both the interface entries and the service-level checks.</translation>
    </message>
</context>
<context>
    <name>Verify</name>
    <message>
        <source>报告文件不存在，可能已被删除或移走。</source>
        <translation>The report file does not exist; it may have been deleted or moved.</translation>
    </message>
    <message>
        <source>文件的 SHA-256 与库里记录的不一致：文件被替换或改动过。</source>
        <translation>The file&apos;s SHA-256 differs from the stored record: the file was replaced or modified.</translation>
    </message>
    <message>
        <source>文件无法解析：不是合法的 PDF，或内容已被破坏。</source>
        <translation>The file cannot be parsed: it is not a valid PDF or its content is damaged.</translation>
    </message>
    <message>
        <source>文件没有数字签名。</source>
        <translation>The file has no digital signature.</translation>
    </message>
    <message>
        <source>文件含有多个签名，本软件生成的报告只有一个签名。</source>
        <translation>The file has several signatures; reports from this software carry exactly one.</translation>
    </message>
    <message>
        <source>文件内容与签名不符：签署之后被修改过。</source>
        <translation>The content does not match the signature: it was modified after signing.</translation>
    </message>
    <message>
        <source>签名本身无效。</source>
        <translation>The signature itself is invalid.</translation>
    </message>
    <message>
        <source>签名没有覆盖整个文件：签署之后被追加过内容。</source>
        <translation>The signature does not cover the whole file: content was appended after signing.</translation>
    </message>
    <message>
        <source>签名证书不是本机信任的那张（可能是别处签的文件）。</source>
        <translation>The signing certificate is not the one trusted on this computer (the file may have been signed elsewhere).</translation>
    </message>
    <message>
        <source>本机还没有签名证书，无法判断证书是否可信。</source>
        <translation>This computer has no signing certificate yet, so trust cannot be judged.</translation>
    </message>
    <message>
        <source>验签通过：签名完整、覆盖整个文件，证书是本机信任的那张，文件与库内记录一致。</source>
        <translation>Verification passed: the signature is intact and covers the whole file, the certificate is the one trusted here, and the file matches the stored record.</translation>
    </message>
    <message>
        <source>验签通过：签名完整、覆盖整个文件，证书是本机信任的那张。</source>
        <translation>Verification passed: the signature is intact and covers the whole file, and the certificate is the one trusted here.</translation>
    </message>
    <message>
        <source>验签未通过：</source>
        <translation>Verification failed:</translation>
    </message>
    <message>
        <source>签名用的是本机自签证书：能证明文件签署后没有被改动，但不具有第三方 CA 的法律效力。</source>
        <translation>Signed with this computer&apos;s self-signed certificate: it proves the file was not changed after signing, but has no third-party CA legal effect.</translation>
    </message>
</context>
<context>
    <name>WellState</name>
    <message>
        <source>空</source>
        <translation>Empty</translation>
    </message>
    <message>
        <source>孵育中</source>
        <translation>Incubating</translation>
    </message>
    <message>
        <source>读数中</source>
        <translation>Reading</translation>
    </message>
    <message>
        <source>阳性</source>
        <translation>Positive</translation>
    </message>
    <message>
        <source>阴性</source>
        <translation>Negative</translation>
    </message>
    <message>
        <source>待复核</source>
        <translation>Pending review</translation>
    </message>
    <message>
        <source>异常</source>
        <translation>Fault</translation>
    </message>
</context>
<context>
    <name>_AuthFrame</name>
    <message>
        <source>凝胶智检系统</source>
        <translation>GelInspect</translation>
    </message>
</context>
<context>
    <name>_DemoRow</name>
    <message>
        <source>点击填入登录框</source>
        <translation>Click to fill in the sign-in form</translation>
    </message>
    <message>
        <source>演示账号 {user}，{role}</source>
        <translation>Demo account {user}, {role}</translation>
    </message>
</context>
<context>
    <name>_HotZone</name>
    <message>
        <source>侧栏热区</source>
        <translation>Sidebar hot zone</translation>
    </message>
</context>
<context>
    <name>_RowThumb</name>
    <message>
        <source>没有这张图</source>
        <translation>No image</translation>
    </message>
    <message>
        <source>翻转前</source>
        <translation>Before flip</translation>
    </message>
    <message>
        <source>翻转后</source>
        <translation>After flip</translation>
    </message>
    <message>
        <source>正在读取图像…</source>
        <translation>Loading images…</translation>
    </message>
</context>
</TS>

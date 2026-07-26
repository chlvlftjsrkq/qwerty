using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace Qwerty.KakaoDeliveryControl
{
    internal static class DeliveryState
    {
        internal static string PauseFile
        {
            get
            {
                string configured = Environment.GetEnvironmentVariable("KAKAO_DELIVERY_PAUSE_FILE");
                if (!string.IsNullOrWhiteSpace(configured))
                {
                    return Environment.ExpandEnvironmentVariables(configured.Trim());
                }

                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(localAppData, "qwerty", "kakao-delivery.pause");
            }
        }

        internal static bool IsPaused
        {
            get { return File.Exists(PauseFile); }
        }

        internal static void Pause()
        {
            string directory = Path.GetDirectoryName(PauseFile);
            Directory.CreateDirectory(directory);
            string json = "{\r\n  \"paused\": true,\r\n  \"paused_at\": \"" +
                DateTimeOffset.UtcNow.ToString("o") + "\"\r\n}\r\n";
            File.WriteAllText(PauseFile, json, new UTF8Encoding(false));
        }

        internal static void Resume()
        {
            if (File.Exists(PauseFile))
            {
                File.Delete(PauseFile);
            }
        }
    }

    internal static class RunnerState
    {
        internal const string TaskName = "GitHubActionsRunner-qwerty";

        internal static string PauseFile
        {
            get
            {
                string configured = Environment.GetEnvironmentVariable("GITHUB_RUNNER_PAUSE_FILE");
                if (!string.IsNullOrWhiteSpace(configured))
                {
                    return Environment.ExpandEnvironmentVariables(configured.Trim());
                }

                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(localAppData, "qwerty", "github-actions-runner.pause");
            }
        }

        private static string RunnerRoot
        {
            get
            {
                string configured = Environment.GetEnvironmentVariable("GITHUB_RUNNER_ROOT");
                if (!string.IsNullOrWhiteSpace(configured))
                {
                    return Environment.ExpandEnvironmentVariables(configured.Trim());
                }
                return @"C:\omx\actions-runner-qwerty";
            }
        }

        internal static bool IsPaused
        {
            get { return File.Exists(PauseFile); }
        }

        internal static bool IsRunning
        {
            get { return HasRunnerProcess("Runner.Listener"); }
        }

        internal static bool IsConnected
        {
            get
            {
                if (!IsRunning)
                {
                    return false;
                }

                try
                {
                    string diagnostics = Path.Combine(RunnerRoot, "_diag");
                    string[] logs = Directory.GetFiles(diagnostics, "Runner_*.log");
                    string latest = null;
                    DateTime latestTime = DateTime.MinValue;
                    foreach (string log in logs)
                    {
                        DateTime modified = File.GetLastWriteTimeUtc(log);
                        if (modified > latestTime)
                        {
                            latest = log;
                            latestTime = modified;
                        }
                    }
                    return latest != null && ReadFileTail(latest, 65536).IndexOf("Listening for Jobs", StringComparison.OrdinalIgnoreCase) >= 0;
                }
                catch
                {
                    return false;
                }
            }
        }

        internal static bool IsWorkerRunning
        {
            get { return HasRunnerProcess("Runner.Worker"); }
        }

        internal static bool TaskExists
        {
            get { return RunTaskCommand("/Query /TN \"" + TaskName + "\"", false).ExitCode == 0; }
        }

        internal static void Pause()
        {
            string directory = Path.GetDirectoryName(PauseFile);
            Directory.CreateDirectory(directory);
            string json = "{\r\n  \"paused\": true,\r\n  \"paused_at\": \"" +
                DateTimeOffset.UtcNow.ToString("o") + "\"\r\n}\r\n";
            File.WriteAllText(PauseFile, json, new UTF8Encoding(false));

            RunTaskCommand("/End /TN \"" + TaskName + "\"", false);
            StopRunnerProcesses("Runner.Worker");
            StopRunnerProcesses("Runner.Listener");

            DateTime deadline = DateTime.UtcNow.AddSeconds(8);
            while (IsRunning && DateTime.UtcNow < deadline)
            {
                Thread.Sleep(250);
            }
            if (IsRunning)
            {
                throw new InvalidOperationException("러너 프로세스를 종료하지 못했습니다.");
            }
        }

        internal static void Resume()
        {
            if (!TaskExists)
            {
                throw new InvalidOperationException("GitHub Actions 러너 예약 작업을 찾지 못했습니다: " + TaskName);
            }

            if (File.Exists(PauseFile))
            {
                File.Delete(PauseFile);
            }

            if (!IsRunning)
            {
                RunTaskCommand("/End /TN \"" + TaskName + "\"", false);
                Thread.Sleep(300);
                TaskCommandResult result = RunTaskCommand("/Run /TN \"" + TaskName + "\"", true);
                if (result.ExitCode != 0)
                {
                    throw new InvalidOperationException("러너 예약 작업을 시작하지 못했습니다.\r\n" + result.ErrorText);
                }
            }

            DateTime deadline = DateTime.UtcNow.AddSeconds(15);
            while (!IsRunning && DateTime.UtcNow < deadline)
            {
                Thread.Sleep(300);
            }
            if (!IsRunning)
            {
                throw new InvalidOperationException("예약 작업을 실행했지만 러너 연결 프로세스가 나타나지 않았습니다.");
            }
        }

        private static bool HasRunnerProcess(string processName)
        {
            Process[] processes = GetRunnerProcesses(processName);
            bool found = processes.Length > 0;
            foreach (Process process in processes)
            {
                process.Dispose();
            }
            return found;
        }

        private static string ReadFileTail(string path, int maximumBytes)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                long offset = Math.Max(0, stream.Length - maximumBytes);
                stream.Seek(offset, SeekOrigin.Begin);
                using (var reader = new StreamReader(stream, Encoding.UTF8, true))
                {
                    return reader.ReadToEnd();
                }
            }
        }

        private static Process[] GetRunnerProcesses(string processName)
        {
            Process[] candidates;
            try
            {
                candidates = Process.GetProcessesByName(processName);
            }
            catch
            {
                return new Process[0];
            }

            var matches = new System.Collections.Generic.List<Process>();
            foreach (Process process in candidates)
            {
                if (IsQwertyRunnerProcess(process))
                {
                    matches.Add(process);
                }
                else
                {
                    process.Dispose();
                }
            }
            return matches.ToArray();
        }

        private static bool IsQwertyRunnerProcess(Process process)
        {
            try
            {
                string root = Path.GetFullPath(RunnerRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string executable = Path.GetFullPath(process.MainModule.FileName);
                return executable.StartsWith(root, StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        private static void StopRunnerProcesses(string processName)
        {
            foreach (Process process in GetRunnerProcesses(processName))
            {
                try
                {
                    process.Kill();
                    process.WaitForExit(5000);
                }
                catch
                {
                }
                finally
                {
                    process.Dispose();
                }
            }
        }

        private sealed class TaskCommandResult
        {
            internal int ExitCode;
            internal string ErrorText;
        }

        private static TaskCommandResult RunTaskCommand(string arguments, bool waitForCompletion)
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = Path.Combine(Environment.SystemDirectory, "schtasks.exe"),
                Arguments = arguments,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            using (Process process = Process.Start(startInfo))
            {
                string output = process.StandardOutput.ReadToEnd();
                string error = process.StandardError.ReadToEnd();
                int timeout = waitForCompletion ? 15000 : 10000;
                if (!process.WaitForExit(timeout))
                {
                    try { process.Kill(); } catch { }
                    return new TaskCommandResult { ExitCode = -1, ErrorText = "작업 스케줄러 응답 시간이 초과됐습니다." };
                }
                string detail = string.IsNullOrWhiteSpace(error) ? output : error;
                return new TaskCommandResult { ExitCode = process.ExitCode, ErrorText = detail.Trim() };
            }
        }
    }

    internal sealed class ControlForm : Form
    {
        private readonly Label deliveryStatusLabel;
        private readonly Button pauseButton;
        private readonly Button resumeButton;
        private readonly Label runnerStatusLabel;
        private readonly Label runnerDetailLabel;
        private readonly Button runnerCheckButton;
        private readonly Button runnerStartButton;
        private readonly Button runnerStopButton;
        private readonly System.Windows.Forms.Timer refreshTimer;

        internal ControlForm()
        {
            Text = "qwerty 자동화 제어";
            ClientSize = new Size(570, 505);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            Font = new Font("Malgun Gothic", 10F, FontStyle.Regular, GraphicsUnit.Point);

            var titleLabel = new Label
            {
                AutoSize = false,
                Location = new Point(22, 16),
                Size = new Size(525, 34),
                Font = new Font("Malgun Gothic", 16F, FontStyle.Bold, GraphicsUnit.Point),
                Text = "qwerty 자동화 제어"
            };

            var deliveryGroup = new GroupBox
            {
                Location = new Point(20, 58),
                Size = new Size(530, 180),
                Text = "카카오톡 자동발송"
            };

            deliveryStatusLabel = new Label
            {
                AutoSize = false,
                Location = new Point(18, 28),
                Size = new Size(490, 30),
                Font = new Font("Malgun Gothic", 12F, FontStyle.Bold, GraphicsUnit.Point)
            };

            var deliveryDescriptionLabel = new Label
            {
                AutoSize = false,
                Location = new Point(18, 62),
                Size = new Size(490, 50),
                ForeColor = Color.FromArgb(75, 75, 75),
                Text = "뉴스 생성과 예약은 유지하고 모닝톡방과 test방 전송만 멈춥니다. 중지 중 지나간 정기 브리핑은 자동 재전송되지 않습니다."
            };

            pauseButton = new Button
            {
                Location = new Point(18, 122),
                Size = new Size(230, 42),
                Text = "전송 끄기",
                UseVisualStyleBackColor = true
            };
            pauseButton.Click += PauseButtonClick;

            resumeButton = new Button
            {
                Location = new Point(280, 122),
                Size = new Size(230, 42),
                Text = "전송 켜기",
                UseVisualStyleBackColor = true
            };
            resumeButton.Click += ResumeButtonClick;

            deliveryGroup.Controls.Add(deliveryStatusLabel);
            deliveryGroup.Controls.Add(deliveryDescriptionLabel);
            deliveryGroup.Controls.Add(pauseButton);
            deliveryGroup.Controls.Add(resumeButton);

            var runnerGroup = new GroupBox
            {
                Location = new Point(20, 252),
                Size = new Size(530, 225),
                Text = "GitHub Actions 러너"
            };

            runnerStatusLabel = new Label
            {
                AutoSize = false,
                Location = new Point(18, 28),
                Size = new Size(490, 30),
                Font = new Font("Malgun Gothic", 12F, FontStyle.Bold, GraphicsUnit.Point)
            };

            runnerDetailLabel = new Label
            {
                AutoSize = false,
                Location = new Point(18, 62),
                Size = new Size(490, 72),
                ForeColor = Color.FromArgb(75, 75, 75)
            };

            runnerCheckButton = new Button
            {
                Location = new Point(18, 151),
                Size = new Size(150, 48),
                Text = "상태 점검",
                UseVisualStyleBackColor = true
            };
            runnerCheckButton.Click += RunnerCheckButtonClick;

            runnerStartButton = new Button
            {
                Location = new Point(189, 151),
                Size = new Size(150, 48),
                Text = "러너 켜기",
                UseVisualStyleBackColor = true
            };
            runnerStartButton.Click += RunnerStartButtonClick;

            runnerStopButton = new Button
            {
                Location = new Point(360, 151),
                Size = new Size(150, 48),
                Text = "러너 끄기",
                UseVisualStyleBackColor = true
            };
            runnerStopButton.Click += RunnerStopButtonClick;

            runnerGroup.Controls.Add(runnerStatusLabel);
            runnerGroup.Controls.Add(runnerDetailLabel);
            runnerGroup.Controls.Add(runnerCheckButton);
            runnerGroup.Controls.Add(runnerStartButton);
            runnerGroup.Controls.Add(runnerStopButton);

            Controls.Add(titleLabel);
            Controls.Add(deliveryGroup);
            Controls.Add(runnerGroup);

            refreshTimer = new System.Windows.Forms.Timer();
            refreshTimer.Interval = 3000;
            refreshTimer.Tick += delegate { RefreshState(); };
            refreshTimer.Start();

            FormClosed += delegate { refreshTimer.Dispose(); };
            RefreshState();
        }

        private void PauseButtonClick(object sender, EventArgs e)
        {
            try
            {
                DeliveryState.Pause();
                RefreshState();
                MessageBox.Show(this, "카카오톡 자동발송을 멈췄습니다. 창을 닫아도 중지 상태가 유지됩니다.", "발송 중지", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                ShowError(ex, "카카오톡 자동발송 제어 오류");
            }
        }

        private void ResumeButtonClick(object sender, EventArgs e)
        {
            try
            {
                DeliveryState.Resume();
                RefreshState();
                MessageBox.Show(this, "카카오톡 자동발송을 다시 켰습니다. 다음 예약 작업부터 정상 발송됩니다.", "발송 재개", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                ShowError(ex, "카카오톡 자동발송 제어 오류");
            }
        }

        private void RunnerCheckButtonClick(object sender, EventArgs e)
        {
            RefreshState();
            string message;
            if (RunnerState.IsPaused)
            {
                message = "GitHub Actions 러너는 사용자가 꺼둔 상태입니다. 재부팅 후에도 자동으로 시작하지 않습니다.";
            }
            else if (RunnerState.IsConnected)
            {
                message = "GitHub Actions 러너가 GitHub에 정상 연결되어 있습니다.";
            }
            else if (RunnerState.IsRunning)
            {
                message = "GitHub Actions 러너 프로세스가 실행 중이며 GitHub 연결을 기다리고 있습니다.";
            }
            else
            {
                message = "GitHub Actions 러너가 실행되지 않고 있습니다. 러너 켜기 버튼으로 다시 시작해 주세요.";
            }
            message += RunnerState.TaskExists ? "\r\n자동 시작 예약도 등록되어 있습니다." : "\r\n자동 시작 예약을 찾지 못했습니다.";
            MessageBox.Show(this, message, "GitHub Actions 상태 점검", MessageBoxButtons.OK, RunnerState.IsRunning ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
        }

        private void RunnerStartButtonClick(object sender, EventArgs e)
        {
            try
            {
                RunnerState.Resume();
                RefreshState();
                MessageBox.Show(this, "GitHub Actions 러너를 켰습니다. 재부팅 후에도 자동으로 시작됩니다.", "러너 시작", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                RefreshState();
                ShowError(ex, "GitHub Actions 러너 시작 오류");
            }
        }

        private void RunnerStopButtonClick(object sender, EventArgs e)
        {
            if (RunnerState.IsWorkerRunning)
            {
                DialogResult answer = MessageBox.Show(
                    this,
                    "현재 GitHub 작업이 실행 중입니다. 지금 끄면 진행 중인 작업이 실패할 수 있습니다. 그래도 끄시겠습니까?",
                    "실행 중인 작업 확인",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning,
                    MessageBoxDefaultButton.Button2);
                if (answer != DialogResult.Yes)
                {
                    return;
                }
            }

            try
            {
                RunnerState.Pause();
                RefreshState();
                MessageBox.Show(this, "GitHub Actions 러너를 껐습니다. 재부팅 후에도 중지 상태가 유지됩니다.", "러너 중지", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                RefreshState();
                ShowError(ex, "GitHub Actions 러너 중지 오류");
            }
        }

        private void RefreshState()
        {
            bool deliveryPaused = DeliveryState.IsPaused;
            deliveryStatusLabel.Text = deliveryPaused ? "현재 상태: 전송 꺼짐" : "현재 상태: 전송 켜짐";
            deliveryStatusLabel.ForeColor = deliveryPaused ? Color.FromArgb(190, 60, 45) : Color.FromArgb(35, 130, 75);
            pauseButton.Enabled = !deliveryPaused;
            resumeButton.Enabled = deliveryPaused;

            bool runnerPaused = RunnerState.IsPaused;
            bool runnerRunning = RunnerState.IsRunning;
            bool runnerConnected = RunnerState.IsConnected;
            bool taskExists = RunnerState.TaskExists;
            if (runnerPaused && runnerRunning)
            {
                runnerStatusLabel.Text = "현재 상태: 중지 요청됨 · 종료 대기 중";
                runnerStatusLabel.ForeColor = Color.FromArgb(190, 95, 35);
            }
            else if (runnerPaused)
            {
                runnerStatusLabel.Text = "현재 상태: 러너 꺼짐";
                runnerStatusLabel.ForeColor = Color.FromArgb(190, 60, 45);
            }
            else if (runnerConnected)
            {
                runnerStatusLabel.Text = "현재 상태: GitHub 정상 연결";
                runnerStatusLabel.ForeColor = Color.FromArgb(35, 130, 75);
            }
            else if (runnerRunning)
            {
                runnerStatusLabel.Text = "현재 상태: GitHub 연결 중";
                runnerStatusLabel.ForeColor = Color.FromArgb(190, 95, 35);
            }
            else
            {
                runnerStatusLabel.Text = "현재 상태: 연결 안 됨";
                runnerStatusLabel.ForeColor = Color.FromArgb(190, 95, 35);
            }

            runnerDetailLabel.Text =
                "뉴스 생성과 예약 실행을 담당합니다. 끄면 재부팅 후에도 중지되며, 켜기를 누르면 즉시 연결합니다.\r\n" +
                "자동 시작 예약: " + (taskExists ? "등록됨" : "없음") + "  ·  GitHub 연결: " + (runnerConnected ? "정상" : (runnerRunning ? "연결 중" : "없음"));
            runnerStartButton.Enabled = runnerPaused || !runnerRunning;
            runnerStopButton.Enabled = runnerRunning || !runnerPaused;
        }

        private void ShowError(Exception ex, string title)
        {
            MessageBox.Show(this, "상태를 변경하지 못했습니다.\r\n\r\n" + ex.Message, title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args != null && args.Length == 1)
            {
                try
                {
                    if (string.Equals(args[0], "--runner-status", StringComparison.OrdinalIgnoreCase))
                    {
                        return RunnerState.IsPaused ? 2 : (RunnerState.IsConnected ? 0 : (RunnerState.IsRunning ? 5 : 3));
                    }
                    if (string.Equals(args[0], "--runner-on", StringComparison.OrdinalIgnoreCase))
                    {
                        RunnerState.Resume();
                        return 0;
                    }
                    if (string.Equals(args[0], "--runner-off", StringComparison.OrdinalIgnoreCase))
                    {
                        if (RunnerState.IsWorkerRunning)
                        {
                            return 4;
                        }
                        RunnerState.Pause();
                        return 0;
                    }
                    return 64;
                }
                catch
                {
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new ControlForm());
            return 0;
        }
    }
}
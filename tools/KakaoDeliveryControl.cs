using System;
using System.Drawing;
using System.IO;
using System.Text;
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

    internal sealed class ControlForm : Form
    {
        private readonly Label statusLabel;
        private readonly Label descriptionLabel;
        private readonly Button pauseButton;
        private readonly Button resumeButton;

        internal ControlForm()
        {
            Text = "카카오톡 자동발송 제어";
            ClientSize = new Size(500, 265);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            Font = new Font("Malgun Gothic", 10F, FontStyle.Regular, GraphicsUnit.Point);

            var titleLabel = new Label
            {
                AutoSize = false,
                Location = new Point(25, 22),
                Size = new Size(450, 32),
                Font = new Font("Malgun Gothic", 15F, FontStyle.Bold, GraphicsUnit.Point),
                Text = "카카오톡 자동발송"
            };

            statusLabel = new Label
            {
                AutoSize = false,
                Location = new Point(27, 66),
                Size = new Size(445, 32),
                Font = new Font("Malgun Gothic", 12F, FontStyle.Bold, GraphicsUnit.Point)
            };

            descriptionLabel = new Label
            {
                AutoSize = false,
                Location = new Point(27, 104),
                Size = new Size(445, 62),
                ForeColor = Color.FromArgb(75, 75, 75),
                Text = "예약과 뉴스 생성은 그대로 유지됩니다. 모닝톡방과 test방 전송만 멈추며, 중지 중 지나간 정기 브리핑은 자동 재전송되지 않습니다."
            };

            pauseButton = new Button
            {
                Location = new Point(27, 185),
                Size = new Size(205, 48),
                Text = "잠시 멈추기",
                UseVisualStyleBackColor = true
            };
            pauseButton.Click += PauseButtonClick;

            resumeButton = new Button
            {
                Location = new Point(267, 185),
                Size = new Size(205, 48),
                Text = "발송 다시 켜기",
                UseVisualStyleBackColor = true
            };
            resumeButton.Click += ResumeButtonClick;

            Controls.Add(titleLabel);
            Controls.Add(statusLabel);
            Controls.Add(descriptionLabel);
            Controls.Add(pauseButton);
            Controls.Add(resumeButton);
            RefreshState();
        }

        private void PauseButtonClick(object sender, EventArgs e)
        {
            try
            {
                DeliveryState.Pause();
                RefreshState();
                MessageBox.Show(
                    this,
                    "카카오톡 자동발송을 잠시 멈췄습니다. 창을 닫아도 중지 상태가 유지됩니다.",
                    "발송 일시정지",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
        }

        private void ResumeButtonClick(object sender, EventArgs e)
        {
            try
            {
                DeliveryState.Resume();
                RefreshState();
                MessageBox.Show(
                    this,
                    "카카오톡 자동발송을 다시 켰습니다. 다음 예약 작업부터 정상 발송됩니다.",
                    "발송 재개",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
        }

        private void RefreshState()
        {
            bool paused = DeliveryState.IsPaused;
            statusLabel.Text = paused ? "현재 상태: 일시정지됨" : "현재 상태: 발송 켜짐";
            statusLabel.ForeColor = paused ? Color.FromArgb(190, 60, 45) : Color.FromArgb(35, 130, 75);
            pauseButton.Enabled = !paused;
            resumeButton.Enabled = paused;
        }

        private void ShowError(Exception ex)
        {
            MessageBox.Show(
                this,
                "상태를 변경하지 못했습니다.\r\n\r\n" + ex.Message,
                "카카오톡 자동발송 제어 오류",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new ControlForm());
        }
    }
}

using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Small launcher; Python, models and dependencies stay in the project environment.
internal static class StudioLauncher
{
    [STAThread]
    private static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
        if (!File.Exists(python))
        {
            MessageBox.Show("请先在项目文件夹双击 setup.bat 安装依赖，再打开本程序。\n请将 AudioTransfer.exe 保留在项目根目录。",
                            "AudioTransfer Studio", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        try
        {
            var start = new ProcessStartInfo(python, "-m audiotransfer.gui");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "AudioTransfer 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}

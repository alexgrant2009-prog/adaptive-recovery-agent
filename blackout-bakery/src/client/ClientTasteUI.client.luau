--!strict
-- ClientTasteUI (LocalScript) -----------------------------------------------
-- Renders taste hints. Like the ticket, this only ever shows content because
-- TasteEvent fired at this client -- and the server only fires it at Tasters.
-- The hint is fuzzy flavour text ("needs something sweeter"), never a number,
-- so it can't be reverse-engineered into the exact recipe.
-------------------------------------------------------------------------------

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local remotes = ReplicatedStorage:WaitForChild("BlackoutBakery"):WaitForChild("Remotes")
local tasteEvent = remotes:WaitForChild(Config.Remotes.Taste) :: RemoteEvent

local playerGui = Players.LocalPlayer:WaitForChild("PlayerGui")

local gui = Instance.new("ScreenGui")
gui.Name = "TasteUI"
gui.ResetOnSpawn = false
gui.Parent = playerGui

local bubble = Instance.new("TextLabel")
bubble.Size = UDim2.fromOffset(360, 60)
bubble.Position = UDim2.new(0.5, -180, 0.72, 0)
bubble.BackgroundColor3 = Color3.fromRGB(70, 40, 90)
bubble.BackgroundTransparency = 0.1
bubble.TextColor3 = Color3.fromRGB(255, 255, 255)
bubble.Font = Enum.Font.GothamBold
bubble.TextSize = 20
bubble.TextWrapped = true
bubble.Text = ""
bubble.TextTransparency = 1
bubble.BackgroundTransparency = 1
bubble.Parent = gui
Instance.new("UICorner", bubble).CornerRadius = UDim.new(0, 12)

local hideToken = 0

tasteEvent.OnClientEvent:Connect(function(hint)
	if type(hint) ~= "string" then
		return
	end
	bubble.Text = '"' .. hint .. '"'

	hideToken += 1
	local myToken = hideToken

	TweenService:Create(bubble, TweenInfo.new(0.15), {
		TextTransparency = 0,
		BackgroundTransparency = 0.1,
	}):Play()

	task.delay(4, function()
		if myToken ~= hideToken then
			return -- a newer hint arrived; let it own the fade
		end
		TweenService:Create(bubble, TweenInfo.new(0.4), {
			TextTransparency = 1,
			BackgroundTransparency = 1,
		}):Play()
	end)
end)

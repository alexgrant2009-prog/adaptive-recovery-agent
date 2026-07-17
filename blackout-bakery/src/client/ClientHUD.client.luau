--!strict
-- ClientHUD (LocalScript) ---------------------------------------------------
-- Shows reputation, shift timer, score, a toast line, and the player's role
-- banner. For Cooks it also shows what's currently in the bowl (not secret --
-- they put it there). It renders lobby / intermission / summary overlays from
-- RoundEvent.
--
-- It also does LOCAL prompt gating: when the player is told their role, it
-- enables only the prompts that role should see (Cook -> bins/oven/trash/serve,
-- Taster -> taste). This is pure feel; the server re-validates every trigger,
-- so a hacker flipping .Enabled back on gains nothing.
-------------------------------------------------------------------------------

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local remotes = ReplicatedStorage:WaitForChild("BlackoutBakery"):WaitForChild("Remotes")
local hudEvent = remotes:WaitForChild(Config.Remotes.HUD) :: RemoteEvent
local roleEvent = remotes:WaitForChild(Config.Remotes.Role) :: RemoteEvent
local roundEvent = remotes:WaitForChild(Config.Remotes.Round) :: RemoteEvent
local bowlEvent = remotes:WaitForChild(Config.Remotes.Bowl) :: RemoteEvent

local playerGui = Players.LocalPlayer:WaitForChild("PlayerGui")

-- UI scaffold --------------------------------------------------------------
local gui = Instance.new("ScreenGui")
gui.Name = "HUD"
gui.ResetOnSpawn = false
gui.Parent = playerGui

local function label(parent: Instance, size: UDim2, pos: UDim2, textSize: number): TextLabel
	local l = Instance.new("TextLabel")
	l.Size = size
	l.Position = pos
	l.BackgroundTransparency = 1
	l.Font = Enum.Font.GothamBold
	l.TextSize = textSize
	l.TextColor3 = Color3.fromRGB(255, 255, 255)
	l.TextStrokeTransparency = 0.4
	l.Parent = parent
	return l
end

-- Top bar.
local topBar = Instance.new("Frame")
topBar.Size = UDim2.new(1, 0, 0, 44)
topBar.BackgroundColor3 = Color3.fromRGB(25, 25, 35)
topBar.BackgroundTransparency = 0.25
topBar.BorderSizePixel = 0
topBar.Parent = gui

local repLabel = label(topBar, UDim2.fromOffset(220, 44), UDim2.fromOffset(16, 0), 20)
repLabel.TextXAlignment = Enum.TextXAlignment.Left
repLabel.Text = "Rep: -"

local timerLabel = label(topBar, UDim2.fromOffset(200, 44), UDim2.new(0.5, -100, 0, 0), 22)
timerLabel.Text = "Shift --:--"

local scoreLabel = label(topBar, UDim2.fromOffset(220, 44), UDim2.new(1, -236, 0, 0), 20)
scoreLabel.TextXAlignment = Enum.TextXAlignment.Right
scoreLabel.Text = "Score: 0"

-- Role banner.
local roleBanner = label(gui, UDim2.new(1, 0, 0, 28), UDim2.fromOffset(0, 48), 20)
roleBanner.Text = ""

-- Toast line.
local toast = label(gui, UDim2.new(1, 0, 0, 28), UDim2.fromOffset(0, 78), 18)
toast.TextColor3 = Color3.fromRGB(255, 235, 150)
toast.Text = ""

-- Bowl panel (Cooks only).
local bowlPanel = Instance.new("Frame")
bowlPanel.Size = UDim2.fromOffset(220, 200)
bowlPanel.Position = UDim2.new(1, -244, 0.5, -100)
bowlPanel.BackgroundColor3 = Color3.fromRGB(30, 30, 40)
bowlPanel.BackgroundTransparency = 0.2
bowlPanel.BorderSizePixel = 0
bowlPanel.Visible = false
bowlPanel.Parent = gui
Instance.new("UICorner", bowlPanel).CornerRadius = UDim.new(0, 10)

local bowlTitle = label(bowlPanel, UDim2.new(1, 0, 0, 26), UDim2.fromOffset(0, 6), 16)
bowlTitle.Text = "In the Bowl"

local bowlList = Instance.new("TextLabel")
bowlList.Size = UDim2.new(1, -20, 1, -40)
bowlList.Position = UDim2.fromOffset(12, 34)
bowlList.BackgroundTransparency = 1
bowlList.Font = Enum.Font.GothamMedium
bowlList.TextSize = 16
bowlList.TextColor3 = Color3.fromRGB(220, 220, 230)
bowlList.TextXAlignment = Enum.TextXAlignment.Left
bowlList.TextYAlignment = Enum.TextYAlignment.Top
bowlList.Text = "(empty)"
bowlList.Parent = bowlPanel

-- Center overlay (lobby / intermission / summary).
local overlay = Instance.new("TextLabel")
overlay.Size = UDim2.new(0.6, 0, 0, 120)
overlay.Position = UDim2.new(0.2, 0, 0.35, 0)
overlay.BackgroundColor3 = Color3.fromRGB(20, 20, 30)
overlay.BackgroundTransparency = 0.15
overlay.Font = Enum.Font.GothamBold
overlay.TextSize = 24
overlay.TextColor3 = Color3.fromRGB(255, 255, 255)
overlay.TextWrapped = true
overlay.Text = ""
overlay.Visible = false
overlay.Parent = gui
Instance.new("UICorner", overlay).CornerRadius = UDim.new(0, 14)

-- Local prompt gating ------------------------------------------------------
local bakery = Workspace:WaitForChild("Bakery")

local function eachPrompt(fn: (ProximityPrompt, string) -> ())
	for _, d in bakery:GetDescendants() do
		if d:IsA("ProximityPrompt") then
			local purpose = d:GetAttribute("Purpose")
			if typeof(purpose) == "string" then
				fn(d, purpose)
			end
		end
	end
end

local function applyRoleGating(canCook: boolean, canTaste: boolean)
	eachPrompt(function(prompt, purpose)
		if purpose == "cook" then
			prompt.Enabled = canCook
		elseif purpose == "taste" then
			prompt.Enabled = canTaste
		end
	end)
end

local function disableGameplayPrompts()
	eachPrompt(function(prompt, purpose)
		if purpose == "cook" or purpose == "taste" then
			prompt.Enabled = false
		end
	end)
end

-- Event handlers -----------------------------------------------------------
local function fmtTime(seconds: number): string
	seconds = math.max(0, math.floor(seconds))
	return string.format("%d:%02d", seconds // 60, seconds % 60)
end

hudEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end
	if payload.rep ~= nil then
		local repMax = payload.repMax or 0
		local rep = math.clamp(payload.rep, 0, repMax)
		local hearts = string.rep("<3 ", rep) .. string.rep("x ", repMax - rep)
		repLabel.Text = "Rep: " .. hearts
	end
	if payload.score ~= nil then
		scoreLabel.Text = "Score: " .. payload.score
	end
	if payload.shiftLeft ~= nil then
		timerLabel.Text = "Shift " .. fmtTime(payload.shiftLeft)
	end
	if payload.toast ~= nil then
		toast.Text = payload.toast
		task.delay(3, function()
			if toast.Text == payload.toast then
				toast.Text = ""
			end
		end)
	end
end)

roleEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end
	overlay.Visible = false
	local role = payload.role or "?"
	local blurb = if payload.isReader
			then "You READ the ticket. Call it out -- you can't cook."
		elseif payload.canCook then "You COOK. You never see the ticket. Listen up."
		else "You TASTE. Give hints. You never see the ticket."
	roleBanner.Text = string.format("YOU ARE THE %s  --  %s", string.upper(role), blurb)
	bowlPanel.Visible = payload.canCook == true
	applyRoleGating(payload.canCook == true, payload.canTaste == true)
end)

bowlEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end
	local contents = payload.contents or {}
	local keys = {}
	for k in contents do
		table.insert(keys, k)
	end
	table.sort(keys)
	if #keys == 0 then
		bowlList.Text = "(empty)"
	else
		local rows = {}
		for _, k in keys do
			table.insert(rows, string.format("%s  x%d", k, contents[k]))
		end
		bowlList.Text = table.concat(rows, "\n")
	end
	bowlTitle.Text = if payload.inOven then "In the Oven (baking)" else "In the Bowl"
end)

roundEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end
	local phase = payload.phase

	if phase == "lobby" then
		disableGameplayPrompts()
		roleBanner.Text = ""
		bowlPanel.Visible = false
		overlay.Visible = true
		overlay.Text = string.format(
			"BLACKOUT BAKERY\n\nStep on the Ready Pad to start.\nReady: %d / %d players",
			payload.ready or 0,
			payload.total or 0
		)
	elseif phase == "round" then
		overlay.Visible = false
	elseif phase == "intermission" then
		disableGameplayPrompts()
		overlay.Visible = true
		overlay.Text = string.format(
			"End of shift %d\nScore: %d\n\nRoles rotate -- new round in %ds...",
			payload.roundIndex or 0,
			payload.score or 0,
			payload.seconds or 0
		)
	elseif phase == "summary" then
		disableGameplayPrompts()
		bowlPanel.Visible = false
		roleBanner.Text = ""
		overlay.Visible = true
		overlay.Text = string.format(
			"SHIFT OVER\n%s\n\nRounds: %d\nFinal Score: %d\n\nReady up to play again.",
			payload.reason or "",
			payload.rounds or 0,
			payload.score or 0
		)
	end
end)

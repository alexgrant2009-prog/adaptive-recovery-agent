--!strict
-- ClientTicketUI (LocalScript) ----------------------------------------------
-- Renders the order ticket -- but ONLY if TicketEvent actually fires at this
-- client. The server fires that event at the Reader alone, so this frame can
-- only ever appear for the Reader. There is no client-side "am I the Reader?"
-- check hiding a frame everyone received; a Cook opening the dev console finds
-- no ticket data at all, because none was ever sent to them.
-------------------------------------------------------------------------------

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local remotes = ReplicatedStorage:WaitForChild("BlackoutBakery"):WaitForChild("Remotes")
local ticketEvent = remotes:WaitForChild(Config.Remotes.Ticket) :: RemoteEvent

local playerGui = Players.LocalPlayer:WaitForChild("PlayerGui")

-- Build UI -----------------------------------------------------------------
local gui = Instance.new("ScreenGui")
gui.Name = "TicketUI"
gui.ResetOnSpawn = false
gui.Parent = playerGui

local frame = Instance.new("Frame")
frame.Size = UDim2.fromOffset(280, 240)
frame.Position = UDim2.new(0, 24, 0.5, -120)
frame.BackgroundColor3 = Color3.fromRGB(250, 248, 235)
frame.BorderSizePixel = 0
frame.Visible = false
frame.Parent = gui
Instance.new("UICorner", frame).CornerRadius = UDim.new(0, 10)

local pad = Instance.new("UIPadding", frame)
pad.PaddingTop = UDim.new(0, 12)
pad.PaddingBottom = UDim.new(0, 12)
pad.PaddingLeft = UDim.new(0, 14)
pad.PaddingRight = UDim.new(0, 14)

local layout = Instance.new("UIListLayout", frame)
layout.SortOrder = Enum.SortOrder.LayoutOrder
layout.Padding = UDim.new(0, 6)

local header = Instance.new("TextLabel")
header.LayoutOrder = 1
header.Size = UDim2.new(1, 0, 0, 22)
header.BackgroundTransparency = 1
header.Font = Enum.Font.GothamBold
header.TextSize = 14
header.TextColor3 = Color3.fromRGB(150, 90, 40)
header.TextXAlignment = Enum.TextXAlignment.Left
header.Text = "-- ORDER TICKET --"
header.Parent = frame

local nameLabel = Instance.new("TextLabel")
nameLabel.LayoutOrder = 2
nameLabel.Size = UDim2.new(1, 0, 0, 28)
nameLabel.BackgroundTransparency = 1
nameLabel.Font = Enum.Font.GothamBlack
nameLabel.TextSize = 22
nameLabel.TextColor3 = Color3.fromRGB(40, 40, 40)
nameLabel.TextXAlignment = Enum.TextXAlignment.Left
nameLabel.Text = ""
nameLabel.Parent = frame

local linesLabel = Instance.new("TextLabel")
linesLabel.LayoutOrder = 3
linesLabel.Size = UDim2.new(1, 0, 0, 120)
linesLabel.BackgroundTransparency = 1
linesLabel.Font = Enum.Font.GothamMedium
linesLabel.TextSize = 18
linesLabel.TextColor3 = Color3.fromRGB(60, 60, 60)
linesLabel.TextXAlignment = Enum.TextXAlignment.Left
linesLabel.TextYAlignment = Enum.TextYAlignment.Top
linesLabel.Text = ""
linesLabel.Parent = frame

local timerLabel = Instance.new("TextLabel")
timerLabel.LayoutOrder = 4
timerLabel.Size = UDim2.new(1, 0, 0, 24)
timerLabel.BackgroundTransparency = 1
timerLabel.Font = Enum.Font.GothamBold
timerLabel.TextSize = 18
timerLabel.TextColor3 = Color3.fromRGB(190, 60, 60)
timerLabel.TextXAlignment = Enum.TextXAlignment.Left
timerLabel.Text = ""
timerLabel.Parent = frame

-- Handle ticket data -------------------------------------------------------
local currentRecipeId: string? = nil

ticketEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end

	if payload.cleared then
		frame.Visible = false
		currentRecipeId = nil
		return
	end

	-- Full ticket (has lines): a new order.
	if payload.lines then
		currentRecipeId = payload.recipeId
		nameLabel.Text = payload.name or "?"
		local body = {}
		for _, line in payload.lines do
			table.insert(body, "-  " .. line)
		end
		linesLabel.Text = table.concat(body, "\n")
		frame.Visible = true
	end

	-- Countdown update (full or light). Ignore stale updates for a cleared order.
	if payload.timeLeft ~= nil and (payload.recipeId == nil or payload.recipeId == currentRecipeId) then
		timerLabel.Text = string.format("Patience: %ds", math.max(0, payload.timeLeft))
	end
end)

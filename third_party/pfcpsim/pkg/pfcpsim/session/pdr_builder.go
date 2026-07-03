// SPDX-License-Identifier: Apache-2.0
// Copyright 2022-present Open Networking Foundation

package session

import (
	"net"
	"os"
	"strconv"

	"github.com/omec-project/pfcpsim/logger"
	"github.com/wmnsk/go-pfcp/ie"
)

// dnnIE returns a Network Instance (DNN) IE for the PDI when PFCPSIM_DNN is set.
// Open5GS allocates the UE IP per-DNN and rejects a session whose PDRs carry no
// Network Instance ("All IP addresses in all subnets are occupied"); it FQDN-decodes
// the value, matching what its own SMF sends. OMEC/SD-Core and OAI don't need it, so
// it is only emitted when the env var is set.
func dnnIE() *ie.IE {
	if d := os.Getenv("PFCPSIM_DNN"); d != "" {
		return ie.NewNetworkInstanceFQDN(d)
	}
	return nil
}

// qfiIE returns a QFI IE for the uplink PDI when PFCPSIM_QFI is set. Open5GS's 5G-SA
// SMF carries the QFI in the access PDI (not just the QER); without it the UPF accepts
// the session but never builds the QoS flow, so gtp5g is left unprogrammed. OMEC/SD-Core
// and OAI don't require it, so it is only emitted when the env var is set.
func qfiIE() *ie.IE {
	if q := os.Getenv("PFCPSIM_QFI"); q != "" {
		v, err := strconv.Atoi(q)
		if err == nil && v > 0 {
			return ie.NewQFI(uint8(v))
		}
	}
	return nil
}

type pdrBuilder struct {
	precedence uint32
	method     IEMethod
	sdfFilter  string
	id         uint16
	teid       uint32
	farID      uint32

	qerIDs []*ie.IE

	ueAddress string
	n3Address string
	direction direction
}

var doCheck = true

func SetCheck(check bool) {
	doCheck = check
}

const (
	PdrNoFuzz         = 0
	PdrWithPrecedence = 1
	PdrWithTEID       = 2
	PdrAddQERID       = 3
	PdrWithFARID      = 4
	PdrMax            = 5
)

func NewPDRBuilder() *pdrBuilder {
	return &pdrBuilder{
		qerIDs: make([]*ie.IE, 0),
	}
}

func (b *pdrBuilder) FuzzIE(ieType int, arg uint) *pdrBuilder {
	switch ieType {
	case PdrWithPrecedence:
		logger.PfcpsimLog.Infoln("PdrWithPrecedence")
		return b.WithPrecedence(uint32(arg))
	case PdrWithTEID:
		logger.PfcpsimLog.Infoln("PdrWithTEID")
		return b.WithTEID(uint32(arg))
	case PdrAddQERID:
		logger.PfcpsimLog.Infoln("PdrAddQERID")
		return b.AddQERID(uint32(arg))
	case PdrWithFARID:
		logger.PfcpsimLog.Infoln("PdrWithFARID")
		return b.WithFARID(uint32(arg))
	default:
	}

	return b
}

func (b *pdrBuilder) WithPrecedence(precedence uint32) *pdrBuilder {
	b.precedence = precedence
	return b
}

func (b *pdrBuilder) WithSDFFilter(filter string) *pdrBuilder {
	b.sdfFilter = filter
	return b
}

func (b *pdrBuilder) WithID(id uint16) *pdrBuilder {
	b.id = id
	return b
}

func (b *pdrBuilder) WithTEID(teid uint32) *pdrBuilder {
	b.teid = teid
	return b
}

func (b *pdrBuilder) WithMethod(method IEMethod) *pdrBuilder {
	b.method = method
	return b
}

func (b *pdrBuilder) WithN3Address(n3Address string) *pdrBuilder {
	b.n3Address = n3Address
	return b
}

func (b *pdrBuilder) WithUEAddress(ueAddress string) *pdrBuilder {
	b.ueAddress = ueAddress
	return b
}

func (b *pdrBuilder) AddQERID(qerID uint32) *pdrBuilder {
	b.qerIDs = append(b.qerIDs, ie.NewQERID(qerID))
	return b
}

func (b *pdrBuilder) WithFARID(farID uint32) *pdrBuilder {
	b.farID = farID
	return b
}

func (b *pdrBuilder) MarkAsDownlink() *pdrBuilder {
	b.direction = downlink
	return b
}

func (b *pdrBuilder) MarkAsUplink() *pdrBuilder {
	b.direction = uplink
	return b
}

func (b *pdrBuilder) validate() {
	if b.direction == notSet {
		logger.PfcpsimLog.Panicln("tried building a PDR without marking it as uplink or downlink")
	}

	if len(b.qerIDs) == 0 {
		logger.PfcpsimLog.Panicln("tried building PDR without providing QER IDs")
	}

	if b.farID == 0 {
		logger.PfcpsimLog.Panicln("tried building PDR without providing FAR ID")
	}

	if b.direction == downlink {
		if b.ueAddress == "" {
			logger.PfcpsimLog.Panicln("tried building downlink PDR without setting the UE IP address")
		}
	}

	if b.direction == uplink {
		if b.n3Address == "" {
			logger.PfcpsimLog.Panicln("tried building uplink PDR without setting the N3Address")
		}

		if b.teid == 0 {
			logger.PfcpsimLog.Panicln("tried building uplink PDR without setting the TEID")
		}
	}
}

func newRemovePDR(pdr *ie.IE) *ie.IE {
	return ie.NewRemovePDR(pdr)
}

// BuildPDR returns by default an UplinkFAR.
// Returns a DownlinkFAR if MarkAsDownlink was invoked.
func (b *pdrBuilder) BuildPDR() *ie.IE {
	if doCheck {
		b.validate()
	}

	createFunc := ie.NewCreatePDR
	if b.method == Update {
		createFunc = ie.NewUpdatePDR
	}

	if b.direction == downlink {
		pdi := ie.NewPDI(
			ie.NewSourceInterface(ie.SrcInterfaceCore),
			ie.NewUEIPAddress(0x2, b.ueAddress, "", 0, 0),
		)

		if b.sdfFilter != "" {
			pdi.Add(ie.NewSDFFilter(b.sdfFilter, "", "", "", 1))
		}
		if ni := dnnIE(); ni != nil {
			pdi.Add(ni)
		}

		pdr := createFunc(
			ie.NewPDRID(b.id),
			ie.NewPrecedence(b.precedence),
			ie.NewFARID(b.farID),
		)

		pdr.Add(pdi)
		pdr.Add(b.qerIDs...)

		if b.method == Delete {
			return newRemovePDR(pdr)
		}

		return pdr
	}

	// UplinkPDR
	// By default pfcpsim provides an explicit UL F-TEID (TEID + N3 addr), which OMEC/
	// SD-Core and OAI accept. Open5GS/free5GC (gtp5g) only program the kernel datapath
	// when the SMF sets the CHOOSE (CH) flag and lets the UPF allocate the UL TEID; given
	// an explicit F-TEID they accept the PFCP session but never install the gtp5g PDR.
	// PFCPSIM_FTEID_CHOOSE switches to a CH F-TEID; the UPF-allocated TEID is then read
	// back from the UPF (e.g. /proc/gtp5g/pdr) to align the N3 traffic.
	fteid := ie.NewFTEID(0x01, b.teid, net.ParseIP(b.n3Address), nil, 0)
	if os.Getenv("PFCPSIM_FTEID_CHOOSE") != "" {
		// CH+CHID (V4) — the form Open5GS's own SMF sends (flags 0x0d + a Choose ID).
		// The UPF allocates the UL F-TEID and programs gtp5g; bare CH (0x04) is rejected.
		fteid = ie.NewFTEID(0x0d, 0, nil, nil, uint8(b.id))
	}
	pdi := ie.NewPDI(
		ie.NewSourceInterface(ie.SrcInterfaceAccess),
		fteid,
	)

	if b.sdfFilter != "" {
		pdi.Add(ie.NewSDFFilter(b.sdfFilter, "", "", "", 1))
	}
	if ni := dnnIE(); ni != nil {
		pdi.Add(ni)
	}
	if qfi := qfiIE(); qfi != nil {
		pdi.Add(qfi)
	}

	pdr := createFunc(
		ie.NewPDRID(b.id),
		ie.NewPrecedence(b.precedence),
		ie.NewOuterHeaderRemoval(0, 0),
		ie.NewFARID(b.farID),
	)

	pdr.Add(pdi)
	pdr.Add(b.qerIDs...)

	if b.method == Delete {
		newRemovePDR(pdr)
	}

	return pdr
}

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
from optimizer_storage import BLOCK, split_pieces


class SplitGeometryTests(unittest.TestCase):
    def test_champion_and_shared_hub_allocations(self):
        for weights, blocks in [([10,5,5,4], [11,6,6,4]),
                                ([10,5,5,4,4], [9,5,5,4,4]),
                                ([10,5,5,2,2], [11,6,6,2,2])]:
            rows = split_pieces(27*BLOCK, weights, [2]+[1]*(len(weights)-1))
            self.assertEqual([sum(size for _,size in row)//BLOCK for row in rows], blocks)
            # Odd internal shares split 5+6 or 4+5, not 6+5 or 5+4.
            self.assertEqual(rows[0][0][1]//BLOCK, blocks[0]//2)
            self.assertEqual(rows[0][-1][1]//BLOCK, blocks[0]-blocks[0]//2)

    def test_ties_rotate_by_task_index(self):
        got = [split_pieces(4*BLOCK, [1,1,1], [1,1,1], task_index=i) for i in range(4)]
        self.assertEqual([[sum(n for _,n in row)//BLOCK for row in plan] for plan in got],
                         [[2,1,1], [1,2,1], [1,1,2], [2,1,1]])

    def test_subblock_tail_goes_to_first_largest_share_then_last_piece(self):
        self.assertEqual(split_pieces(4*BLOCK+17, [1,1], [2,1]),
                         [[[0,BLOCK], [BLOCK,BLOCK+17]], [[2*BLOCK+17,2*BLOCK]]])

    def test_too_many_subpieces_are_skipped_until_last(self):
        self.assertEqual(split_pieces(BLOCK, [1], [8]), [[[0,BLOCK]]])
        self.assertEqual(split_pieces(17, [1,1], [8,1]), [[[0,17]], []])

    def test_every_byte_covered_once_with_rotated_and_small_allocations(self):
        for length in (1, BLOCK-1, BLOCK, 9*BLOCK+7, 27*BLOCK):
            for i in range(8):
                rows=split_pieces(length, [1,3,5,7,9], [8,3,2,1,4], i)
                cursor=0
                for row in rows:
                    for start, size in row:
                        self.assertEqual(start,cursor)
                        self.assertGreater(size,0)
                        cursor += size
                self.assertEqual(cursor,length)

    def test_invalid_geometry_is_rejected(self):
        for length, weights, pieces, index in [(True,[1],[1],0), (0,[1],[1],0),
                (BLOCK,[True],[1],0), (BLOCK,[1],[0],0), (BLOCK,[1],[1],-1),
                (BLOCK,[1],[1],True), (BLOCK,[1,2],[1],0)]:
            with self.assertRaises(ValueError): split_pieces(length,weights,pieces,index)


if __name__ == '__main__': unittest.main()
